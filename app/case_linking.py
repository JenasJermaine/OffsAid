"""OffsAid cross-case linking: have we seen this contact, payment account, letter or wording before?

Public functions
    process_case(conn, case, key=None)   find links, then store the case (use this in the pipeline)
    find_links(conn, case, key=None)     matching only, stores nothing
    record_case(conn, case, key=None)    store only (used by the seed script)
    player_view(result)                  the safe subset to show the player
    set_status(conn, case_id, status)    KEFWA review outcome
    delete_case(conn, case_id)           erasure request

`conn` is a psycopg 3 connection. The SQL is plain Postgres, so if the repo uses SQLAlchemy or asyncpg,
keep the SQL and change only the thin execute/fetch helpers at the bottom of the file.

Design rules (see the build doc, section 5):
  * raw phone numbers, accounts and emails are never stored, only HMAC-SHA256 hashes made with a secret key;
  * a repeat is not automatically suspicious: the flag is "one identifier under different club names", a match
    with a KEFWA-confirmed case, or a letter template reused under a different club name;
  * the report states facts and never a verdict. Thresholds below are untested starting points.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

# ----------------------------------------------------------------------------- configuration
PHASH_MAX_DISTANCE = 10          # bits (of 64) that may differ for "same layout". Starting guess, tune on seeded cases.
TEXT_SIMILARITY_MIN = 0.6        # pg_trgm similarity of masked letter text. Starting guess.
WORDING_SIMILARITY_MIN = 0.6     # pg_trgm similarity of masked agent messages. Starting guess.
SAME_NAME_RATIO = 85             # RapidFuzz token_sort_ratio at or above this = same club / agent name.
ESCALATE_STATUSES = {"confirmed_scam"}   # a match with these statuses sends the case to KEFWA. Add "reported" if the team agrees.
INFO_STATUSES = {"confirmed_scam", "reported"}

FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "outlook.com", "hotmail.com", "live.com",
             "msn.com", "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "mail.com",
             "yandex.com", "zoho.com", "gmx.com"}

ID_KINDS = ("phone", "account", "email", "domain")
SUBJECT = {"phone": "This phone number", "account": "This payment account",
           "email": "This email address", "domain": "This sender domain"}
STRENGTH = {"phone": "strong", "account": "strong", "email": "strong", "domain": "medium",
            "letter_template": "strong", "wording": "medium"}


# ----------------------------------------------------------------------------- input type
@dataclass
class CaseInput:
    """Everything linking needs about one case. Build it from the extraction output and the check results."""
    source: str = "live"                       # live | kefwa_past | public_warning | simulated
    status: str = "unreviewed"
    external_ref: Optional[str] = None         # e.g. WhatsApp message id, for idempotency
    claimed_club: Optional[str] = None
    claimed_agent: Optional[str] = None
    # raw identifiers: [{"kind": "phone|account|email|domain", "value": "...", "role": "agent_contact|payment_to|sender"}]
    identifiers: list[dict] = field(default_factory=list)
    letter_text: Optional[str] = None          # text of the letter (OCR / PDF text / extracted by the vision LLM)
    letter_phash: Optional[int] = None         # from image_phash(page_1_image)
    wording_text: Optional[str] = None         # the AGENT's messages joined with newlines
    wording_labels: list[str] = field(default_factory=list)   # labels from the scam-language check
    mask_names: list[str] = field(default_factory=list)       # extra names to mask: player name, signatory...
    source_url: Optional[str] = None
    summary: dict = field(default_factory=dict)               # extracted fields and check results (no player identity)


# ----------------------------------------------------------------------------- hashing and normalising
def get_hash_key() -> bytes:
    key = os.environ.get("OFFSAID_HASH_KEY", "")
    if len(key) < 32:
        raise RuntimeError("OFFSAID_HASH_KEY must be set to a secret of at least 32 characters "
                           "(generate: python -c \"import secrets; print(secrets.token_hex(32))\"). "
                           "Losing or changing it makes every stored hash unmatchable.")
    return key.encode()


def hash_identifier(kind: str, value: str, key: Optional[bytes] = None) -> str:
    """HMAC-SHA256, not a plain hash: phone numbers are few enough to brute-force a plain hash."""
    return hmac.new(key or get_hash_key(), f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def normalise_phone(raw: str) -> Optional[str]:
    """07xx / +254 / spaced formats -> E.164. Unparseable but long enough -> digits only. Else None."""
    import phonenumbers
    s = (raw or "").strip()
    if not s:
        return None
    try:
        num = phonenumbers.parse(s, "KE")
        if phonenumbers.is_possible_number(num):
            return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    digits = re.sub(r"\D", "", s)
    return digits if len(digits) >= 7 else None


def _looks_like_phone(raw: str) -> bool:
    """Kenyan local (07xx / 01xx), 254 + 9 digits, or any number written with a leading +."""
    d = re.sub(r"\D", "", raw or "")
    if (raw or "").strip().startswith("+"):
        return 9 <= len(d) <= 15
    return (len(d) == 10 and d.startswith(("07", "01"))) or (len(d) == 12 and d.startswith("254"))


def normalise_email(raw: str) -> Optional[str]:
    s = (raw or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s):
        return None
    local, domain = s.rsplit("@", 1)
    if domain in {"gmail.com", "googlemail.com"}:          # gmail ignores dots and +tags: scammers use that
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def normalise_domain(raw: str) -> Optional[str]:
    s = (raw or "").strip().lower()
    s = re.sub(r"^[a-z]+://", "", s).split("/", 1)[0].split("?", 1)[0]
    s = re.sub(r"^www\.", "", s)
    return s if "." in s and s not in FREE_MAIL else None


def canonical_identifier(kind: str, raw: str) -> Optional[tuple[str, str]]:
    """-> (kind, normalised value) or None. A payment account that is really a mobile number becomes a 'phone',
    so 'pay to this M-Pesa number' matches an earlier case where that number was the agent's contact."""
    if kind == "phone":
        v = normalise_phone(raw)
        return ("phone", v) if v else None
    if kind == "account":
        if _looks_like_phone(raw):
            v = normalise_phone(raw)
            return ("phone", v) if v else None
        d = re.sub(r"\D", "", raw or "")
        return ("account", d) if len(d) >= 4 else None
    if kind == "email":
        v = normalise_email(raw)
        return ("email", v) if v else None
    if kind == "domain":
        v = normalise_domain(raw)
        return ("domain", v) if v else None
    return None


def canonical_identifiers(case: CaseInput) -> list[tuple[str, str, Optional[str]]]:
    """Normalised, de-duplicated (kind, value, role). An email also yields its domain (unless free-mail)."""
    out: dict[tuple[str, str], Optional[str]] = {}
    for item in case.identifiers:
        c = canonical_identifier(item.get("kind", ""), item.get("value", ""))
        if not c:
            continue
        out.setdefault(c, item.get("role"))
        if c[0] == "email":
            d = normalise_domain(c[1].rsplit("@", 1)[1])
            if d:
                out.setdefault(("domain", d), item.get("role"))
    return [(k, v, r) for (k, v), r in out.items()]


_TITLES = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "coach", "agent"}
_CLUB_NOISE = {"fc", "afc", "sc", "f", "c", "football", "club"}


def normalise_name(raw: Optional[str], club: bool = False) -> str:
    s = re.sub(r"[^\w\s]", " ", (raw or "").lower())
    toks = [t for t in s.split() if t not in _TITLES and not (club and t in _CLUB_NOISE)]
    return " ".join(toks)


def same_name(a: Optional[str], b: Optional[str], club: bool = False) -> bool:
    from rapidfuzz import fuzz
    na, nb = normalise_name(a, club), normalise_name(b, club)
    return bool(na) and bool(nb) and (na == nb or fuzz.token_sort_ratio(na, nb) >= SAME_NAME_RATIO)


def count_distinct_names(names: list[Optional[str]], club: bool = False) -> int:
    reps: list[str] = []
    for n in names:
        if not normalise_name(n, club):
            continue
        if not any(same_name(n, r, club) for r in reps):
            reps.append(n)  # type: ignore[arg-type]
    return len(reps)


# ----------------------------------------------------------------------------- text masking and image hash
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s\-()]{7,}\d(?![\w])")
_AMT = re.compile(r"(?:\b(?:kes|ksh|usd|eur|gbp)\b\.?\s?|[$€£]\s?)\d[\d,\.]*\s?k?\b|\b\d[\d,\.]*\s?(?:k|kes|ksh|usd|eur|gbp)\b", re.I)
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE = re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTHS}(?:\s+\d{{4}})?\b|\b{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b|"
                   r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b", re.I)


def mask_text(text: str, club: Optional[str] = None, names: Optional[list[str]] = None) -> str:
    """Replace what changes when a letter is reused (club, names, dates, amounts, contacts) with placeholders,
    so two letters from different clubs that share a template come out (almost) identical."""
    t = text or ""
    t = _EMAIL.sub(" <email> ", t)
    t = _URL.sub(" <url> ", t)
    t = _PHONE.sub(" <phone> ", t)
    t = _AMT.sub(" <amt> ", t)
    t = _DATE.sub(" <date> ", t)
    for n, tag in sorted(([(c, "<club>") for c in _club_variants(club)] + [(x, "<name>") for x in (names or []) if x]),
                         key=lambda p: -len(p[0])):
        t = re.sub(rf"(?<!\w){re.escape(n)}(?!\w)", f" {tag} ", t, flags=re.I)
    t = re.sub(r"\d+", " <num> ", t.lower())
    t = re.sub(r"[^\w<>\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _club_variants(club: Optional[str]) -> list[str]:
    if not club:
        return []
    base = re.sub(r"\b(?:a?fc|sc|f\.c\.|football club)\b\.?", "", club, flags=re.I).strip(" .,-")
    return [v for v in {club.strip(), base} if v]


def to_signed64(u: int) -> int:
    return u - (1 << 64) if u >= (1 << 63) else u


def image_phash(image: Any) -> int:
    """64-bit perceptual hash of page 1 (path or PIL image), as a signed int for Postgres bigint.
    Render PDFs to an image first (PyMuPDF or pdf2image). Photos taken at an angle will not match."""
    import imagehash
    from PIL import Image, ImageOps
    img = image if hasattr(image, "convert") else Image.open(image)
    h = imagehash.phash(ImageOps.autocontrast(img.convert("L")), hash_size=8)
    return to_signed64(int("".join("1" if b else "0" for b in h.hash.flatten()), 2))


# ----------------------------------------------------------------------------- storing a case
def record_case(conn, case: CaseInput, key: Optional[bytes] = None) -> int:
    """Insert the case, its hashed identifiers and its document fingerprints. Idempotent on external_ref."""
    from psycopg.types.json import Jsonb
    key = key or get_hash_key()
    with conn.transaction():
        if case.external_ref:
            row = _one(conn, "select id from cases where external_ref = %s", (case.external_ref,))
            if row:
                return row["id"]
        row = _one(conn, """insert into cases (external_ref, source, status, claimed_club, claimed_agent, source_url, summary)
                            values (%s,%s,%s,%s,%s,%s,%s) returning id""",
                   (case.external_ref, case.source, case.status, case.claimed_club, case.claimed_agent,
                    case.source_url, Jsonb(case.summary or {})))
        case_id = row["id"]
        for kind, value, role in canonical_identifiers(case):
            _exec(conn, "insert into identifiers (case_id, kind, value_hash, role) values (%s,%s,%s,%s)",
                  (case_id, kind, hash_identifier(kind, value, key), role))
        if case.letter_text:
            _exec(conn, "insert into documents (case_id, kind, phash, masked_text) values (%s,'letter',%s,%s)",
                  (case_id, case.letter_phash, mask_text(case.letter_text, case.claimed_club, _names(case))))
        if case.wording_text:
            _exec(conn, "insert into documents (case_id, kind, masked_text, labels) values (%s,'wording',%s,%s)",
                  (case_id, mask_text(case.wording_text, case.claimed_club, _names(case)), list(case.wording_labels)))
        return case_id


def _names(case: CaseInput) -> list[str]:
    return [n for n in [case.claimed_agent, *case.mask_names] if n]


def set_status(conn, case_id: int, status: str) -> None:
    """KEFWA review outcome. Statuses: unreviewed | reported | confirmed_scam | verified_genuine."""
    with conn.transaction():
        _exec(conn, "update cases set status = %s, status_updated_at = now() where id = %s", (status, case_id))


def delete_case(conn, case_id: int) -> None:
    """Erasure request: identifiers and documents go with it (on delete cascade)."""
    with conn.transaction():
        _exec(conn, "delete from cases where id = %s", (case_id,))


# ----------------------------------------------------------------------------- finding links
def find_links(conn, case: CaseInput, key: Optional[bytes] = None, exclude_case_id: Optional[int] = None) -> dict:
    """-> {"links": [...], "escalate": bool, "escalation_reasons": [...]}.  Stores nothing."""
    key = key or get_hash_key()
    canon = canonical_identifiers(case)
    links: list[dict] = []
    for kind in ID_KINDS:
        hashes = [hash_identifier(k, v, key) for k, v, _ in canon if k == kind]
        if hashes:
            rows = _all(conn, """select distinct on (c.id) c.id, c.status, c.source, c.claimed_club, c.claimed_agent, c.created_at
                                 from identifiers i join cases c on c.id = i.case_id
                                 where i.kind = %s and i.value_hash = any(%s) and (%s::bigint is null or c.id <> %s::bigint)
                                 order by c.id""", (kind, hashes, exclude_case_id, exclude_case_id))
            if rows:
                links.append(_identifier_link(kind, rows, case))
    if case.letter_text and case.letter_phash is not None:
        rows = _all(conn, """select distinct on (c.id) c.id, c.status, c.source, c.claimed_club, c.claimed_agent, c.created_at
                             from documents d join cases c on c.id = d.case_id
                             where d.kind = 'letter' and d.phash is not null and (%(ex)s::bigint is null or c.id <> %(ex)s::bigint)
                               and bit_count((d.phash # %(ph)s::bigint)::bit(64)) <= %(maxd)s
                               and similarity(d.masked_text, %(txt)s::text) >= %(mins)s
                             order by c.id""",
                    {"ex": exclude_case_id, "ph": case.letter_phash, "maxd": PHASH_MAX_DISTANCE,
                     "txt": mask_text(case.letter_text, case.claimed_club, _names(case)), "mins": TEXT_SIMILARITY_MIN})
        if rows:
            links.append(_letter_link(rows, case))
    if case.wording_text:
        rows = _all(conn, """select distinct on (c.id) c.id, c.status, c.source, c.claimed_club, c.claimed_agent, c.created_at, d.labels
                             from documents d join cases c on c.id = d.case_id
                             where d.kind = 'wording' and (%(ex)s::bigint is null or c.id <> %(ex)s::bigint)
                               and similarity(d.masked_text, %(txt)s::text) >= %(mins)s
                             order by c.id""",
                    {"ex": exclude_case_id, "txt": mask_text(case.wording_text, case.claimed_club, _names(case)),
                     "mins": WORDING_SIMILARITY_MIN})
        if rows:
            links.append(_wording_link(rows, case))
    reasons = _escalation_reasons(links)
    return {"links": links, "escalate": bool(reasons), "escalation_reasons": reasons}


def process_case(conn, case: CaseInput, key: Optional[bytes] = None) -> dict:
    """The pipeline call: match against EARLIER cases, then store this case (so it never matches itself).
    A retried webhook (same external_ref) returns the stored case id and re-runs the match without duplicating."""
    key = key or get_hash_key()
    existing = _one(conn, "select id from cases where external_ref = %s", (case.external_ref,)) if case.external_ref else None
    result = find_links(conn, case, key, exclude_case_id=existing["id"] if existing else None)
    result["case_id"] = existing["id"] if existing else record_case(conn, case, key)
    return result


def _base_link(kind: str, rows: list[dict]) -> dict:
    statuses = Counter(r["status"] for r in rows)
    dates = sorted(r["created_at"] for r in rows)
    return {"type": kind, "strength": STRENGTH[kind], "earlier_cases": len(rows), "statuses": dict(statuses),
            "first_seen": dates[0], "last_seen": dates[-1], "case_ids": [r["id"] for r in rows],
            "involves_escalation_status": any(s in ESCALATE_STATUSES for s in statuses)}


def _identifier_link(kind: str, rows: list[dict], case: CaseInput) -> dict:
    link = _base_link(kind, rows)
    earlier_clubs = count_distinct_names([r["claimed_club"] for r in rows], club=True)
    all_clubs = count_distinct_names([r["claimed_club"] for r in rows] + [case.claimed_club], club=True)
    different_club = all_clubs >= 2
    same_context = (not different_club
                    and all(same_name(case.claimed_agent, r["claimed_agent"]) or not (case.claimed_agent and r["claimed_agent"])
                            for r in rows))
    known = same_context and not any(s in INFO_STATUSES for s in link["statuses"])
    link.update(different_club=different_club, distinct_clubs_earlier=earlier_clubs, known_contact=known,
                flag=(different_club or link["involves_escalation_status"] or any(s in INFO_STATUSES for s in link["statuses"])) and not known)
    link["summary"] = _identifier_summary(kind, link, case)
    return link


def _letter_link(rows: list[dict], case: CaseInput) -> dict:
    link = _base_link("letter_template", rows)
    if case.claimed_club:
        different = any(r["claimed_club"] and not same_name(case.claimed_club, r["claimed_club"], club=True) for r in rows)
    else:
        different = count_distinct_names([r["claimed_club"] for r in rows], club=True) >= 2
    link.update(different_club=bool(different), known_contact=not different, flag=bool(different))
    n = link["earlier_cases"]
    link["summary"] = (f"This letter looks similar to {n} earlier letter{'s' if n != 1 else ''} issued under a different club name."
                       if different else
                       f"This letter looks similar to {n} earlier letter{'s' if n != 1 else ''} from the same club.")
    return link


def _wording_link(rows: list[dict], case: CaseInput) -> dict:
    link = _base_link("wording", rows)
    mine = set(case.wording_labels)
    shared = any(mine & set(r.get("labels") or []) for r in rows)
    link.update(different_club=False, known_contact=False, flag=shared)
    n = link["earlier_cases"]
    link["summary"] = (f"The agent's wording is similar to {n} earlier case{'s' if n != 1 else ''} that showed the same warning signs."
                       if shared else
                       f"The agent's wording is similar to {n} earlier case{'s' if n != 1 else ''}.")
    return link


def _identifier_summary(kind: str, link: dict, case: CaseInput) -> str:
    n, st = link["earlier_cases"], link["statuses"]
    s = f"{SUBJECT[kind]} appeared in {n} earlier case{'s' if n != 1 else ''}"
    if link["known_contact"]:
        return s + " with the same club and agent name. Known contact, no flag."
    if link["different_club"]:
        k = link["distinct_clubs_earlier"]
        s += f", linked to {k} different club names" if k >= 2 else ", under a different club name than in this offer"
    s += "."
    if st.get("confirmed_scam"):
        s += f" KEFWA has confirmed {st['confirmed_scam']} of them as fraud."
    if st.get("reported"):
        s += f" {st['reported']} appear in a report or public warning."
    if not any(st.get(x) for x in ("confirmed_scam", "reported", "verified_genuine")):
        s += " None of them has been reviewed."
    return s


def _escalation_reasons(links: list[dict]) -> list[str]:
    out = []
    for l in links:
        t = l["type"]
        if t in ID_KINDS:
            if l["involves_escalation_status"]:
                out.append(f"{t} appears in a case with a confirmed scam status")
            if l["different_club"]:
                out.append(f"{t} appears under different club names")
        elif t == "letter_template" and l["different_club"]:
            out.append("letter template reused under a different club name")
    return out


def player_view(result: dict) -> dict:
    """What the player may see: sentences and a flag per link. No case ids, dates or other players' details."""
    shown = [{"type": l["type"], "flag": l["flag"], "summary": l["summary"]} for l in result["links"]]
    note = None if shown else "No earlier case matched. Our database is small and new, so this does not mean the offer is safe."
    return {"links": shown, "no_match_note": note, "escalated": result["escalate"]}


# ----------------------------------------------------------------------------- thin SQL helpers (swap these for another driver)
def _cursor(conn):
    from psycopg.rows import dict_row
    return conn.cursor(row_factory=dict_row)


def _exec(conn, sql, params=None):
    with _cursor(conn) as cur:
        cur.execute(sql, params)


def _one(conn, sql, params=None):
    with _cursor(conn) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _all(conn, sql, params=None):
    with _cursor(conn) as cur:
        cur.execute(sql, params)
        return cur.fetchall()
