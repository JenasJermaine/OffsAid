"""Step 2, part 2: check what an uploaded document claims.

check_document() reads the file, extracts its claims (app.document_extraction) and runs
each check below on the extracted fields. Each check is a small function that returns a
status (consistent, not confirmed, contradicted) with the quotes and sources it used.
"Not confirmed" never means fake.

Signatory and club-contact checks need the web-checking agent (Step 3) and are listed
under pending_checks. Template reuse and repeat numbers need the case history (the
cross-case linking step); this module returns the hashed keys that step will store.
"""

from __future__ import annotations

import csv
import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import phonenumbers
from phonenumbers import geocoder
from rapidfuzz import fuzz

from app.config import settings
from app.document_extraction import LoadedDocument, extract_fields, load_document
from app.document_schemas import (
    Check,
    DocumentCheckResponse,
    DocumentFields,
    LinkingKeys,
    PageInfo,
    Quoted,
)
from app.hashing import hash_lookup_value
from app.lookups import ClubFacts, Lookups

ROOT = Path(__file__).resolve().parent.parent
FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "ymail.com", "outlook.com",
    "hotmail.com", "live.com", "msn.com", "icloud.com", "me.com", "aol.com", "protonmail.com",
    "proton.me", "gmx.com", "mail.com", "zoho.com", "yandex.com",
}
SECOND_LEVEL = {"co", "com", "org", "net", "ac", "or", "go", "gov", "edu", "ne", "sc", "info", "my", "id"}
DESIGN_TOOLS = re.compile(r"canva|photoshop|picsart|illustrator|ilovepdf|smallpdf|sejda|pdfescape|pdf ?filler|inkscape", re.I)
COUNTRY_ALIASES = {"uae": "AE", "england": "GB", "scotland": "GB", "wales": "GB", "uk": "GB", "usa": "US"}
LEAGUE_MATCH_MIN = 80
WINDOW_NOT_IN_TABLE = "is not in our registration-window table"


# --- Entry point -------------------------------------------------------------------------


def check_document(
    data: bytes,
    mime_type: str,
    *,
    lookups: Optional[Lookups] = None,
    today: Optional[date] = None,
) -> DocumentCheckResponse:
    lookups = lookups or Lookups()
    today = today or date.today()
    doc = load_document(data, mime_type)
    fields, method, notes = extract_fields(doc)
    club = lookups.club(fields.club_name.value) if fields.club_name else None

    checks: List[Check] = []
    checks.extend(sender_domain_checks(fields, club, lookups, today))
    checks.extend(contact_phone_checks(fields, club))
    checks.append(club_check(fields, club))
    checks.extend(fee_checks(fields, has_text=bool(doc.text.strip() or fields.transcribed_text.strip())))
    window = transfer_window_check(fields, club, load_windows())
    if window:
        checks.append(window)
    dates = date_order_check(fields)
    if dates:
        checks.append(dates)
    checks.extend(detail_checks(fields))
    checks.append(metadata_check(doc.metadata, today))

    summary = {status: sum(c.status == status for c in checks) for status in ("consistent", "not_confirmed", "contradicted")}
    summary["red_flags"] = sum(c.red_flag for c in checks)
    needs_review = bool(summary["contradicted"] or summary["red_flags"] or method == "patterns_only" or not summary["consistent"])
    return DocumentCheckResponse(
        extraction_method=method,
        extraction_notes=notes,
        file=PageInfo(pages=len(doc.page_texts), has_text_layer=doc.has_text_layer, metadata=doc.metadata),
        fields=fields,
        checks=checks,
        summary=summary,
        needs_review=needs_review,
        league_window_missing=window_missing([window] if window else []),
        pending_checks=pending_checks(fields, club, window),
        linking_keys=linking_keys(doc, fields),
    )


# --- Helpers --------------------------------------------------------------------------------


def _q(*values: Optional[Quoted]) -> List[str]:
    return [value.quote for value in values if value is not None]


def _iso(value: Optional[Quoted]) -> Optional[date]:
    if value is None:
        return None
    try:
        return date.fromisoformat(value.value.strip()[:10])
    except ValueError:
        return None


def registrable_domain(host: str) -> str:
    """hjk.fi -> hjk.fi, www.persib.co.id -> persib.co.id, mail.club.com -> club.com."""
    labels = host.lower().strip(".").split(".")
    if labels and labels[0] == "www":
        labels = labels[1:]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _core(domain: str) -> str:
    return domain.split(".")[0].replace("-", "")


@lru_cache(maxsize=1)
def _country_codes() -> Dict[str, str]:
    codes = dict(COUNTRY_ALIASES)
    for region in phonenumbers.SUPPORTED_REGIONS:
        example = phonenumbers.example_number(region)
        if example:
            name = geocoder.country_name_for_number(example, "en")
            if name:
                codes.setdefault(name.casefold(), region)
    return codes


@lru_cache(maxsize=1)
def _region_names() -> Dict[str, str]:
    names: Dict[str, str] = {}
    for name, region in _country_codes().items():
        if name not in COUNTRY_ALIASES:
            names.setdefault(region, name.title())
    return names


def country_code(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return _country_codes().get(name.strip().casefold())


def _club_region(fields: DocumentFields, club: Optional[ClubFacts]) -> Tuple[Optional[str], Optional[str]]:
    if club and club.country_iso2:
        return club.country_iso2, club.country
    stated = fields.club_country.value if fields.club_country else None
    return country_code(stated), stated


# --- Strong checks --------------------------------------------------------------------------


def sender_domain_checks(fields: DocumentFields, club: Optional[ClubFacts], lookups: Lookups, today: date) -> List[Check]:
    email = fields.sender_email
    if email is None or "@" not in email.value:
        return [Check(check="sender_email_domain", status="not_confirmed", strength="strong",
                      note="The document gives no sender email address to compare with the club's website.")]
    domain = email.value.rsplit("@", 1)[1].lower().strip(" .>")
    quotes = [email.quote]
    if domain in FREE_EMAIL_DOMAINS:
        return [Check(
            check="sender_email_domain", status="not_confirmed", strength="strong", red_flag=True, quotes=quotes,
            note=f"The sender uses a free {domain} address, not a club domain. Clubs normally write from "
                 "their own domain. Some small clubs do use free email, so confirm with the club directly.",
        )]
    checks = [_domain_vs_club(fields, domain, quotes, club), _domain_age(domain, lookups, today)]
    return checks


def _domain_vs_club(fields: DocumentFields, domain: str, quotes: List[str], club: Optional[ClubFacts]) -> Check:
    official_host = urlparse(club.official_website).hostname if club and club.official_website else None
    if not official_host:
        return Check(check="sender_email_domain", status="not_confirmed", strength="strong", quotes=quotes,
                     note=f"No official website was found for the club, so {domain} could not be compared.")
    official = registrable_domain(official_host)
    sources = [club.source, club.official_website]
    sender = registrable_domain(domain)
    if sender == official:
        return Check(check="sender_email_domain", status="consistent", strength="strong", quotes=quotes, sources=sources,
                     note=f"The sender domain {domain} matches the club's official website {official}.")
    club_words = [w for w in re.findall(r"[a-z]{4,}", (club.name + " " + (fields.club_name.value if fields.club_name else "")).casefold())
                  if w not in {"club", "football", "united", "city"}]
    lookalike = (
        (len(_core(official)) >= 3 and _core(official) in _core(sender))
        or fuzz.ratio(_core(sender), _core(official)) >= 75
        or any(word in _core(sender) for word in club_words)
    )
    if lookalike:
        return Check(
            check="sender_email_domain", status="contradicted", strength="strong", red_flag=True, quotes=quotes, sources=sources,
            note=f"The sender domain {domain} looks like the club's name but is not its official domain {official}. "
                 "Lookalike domains are a common way to impersonate a club.",
        )
    agency = fields.document_type == "agency_letter"
    return Check(
        check="sender_email_domain", status="not_confirmed", strength="strong", red_flag=not agency, quotes=quotes, sources=sources,
        note=f"The sender domain {domain} is not the club's official domain {official}."
             + (" The letter is from an agency, so a different domain is expected; ask the club to confirm the offer."
                if agency else " Ask the club, through its official website, whether it sent this letter."),
    )


def _domain_age(domain: str, lookups: Lookups, today: date) -> Check:
    registration = lookups.domain_registration(registrable_domain(domain))
    if registration is None:
        return Check(check="sender_domain_age", status="not_confirmed", strength="strong",
                     note=f"No registration date was found for {domain} (RDAP). The domain may not exist or the lookup failed.")
    age = (today - registration.registered).days
    if age < settings.new_domain_days:
        return Check(
            check="sender_domain_age", status="not_confirmed", strength="strong", red_flag=True, sources=[registration.source],
            note=f"{registration.domain} was registered on {registration.registered} ({age} days ago). "
                 "A domain this new is a warning sign for a club letter.",
        )
    return Check(check="sender_domain_age", status="consistent", strength="strong", sources=[registration.source],
                 note=f"{registration.domain} was registered on {registration.registered}; it is not a new domain.")


def contact_phone_checks(fields: DocumentFields, club: Optional[ClubFacts]) -> List[Check]:
    if not fields.contact_phones:
        return [Check(check="contact_phone_country", status="not_confirmed", strength="strong",
                      note="The document gives no contact phone number.")]
    region, country = _club_region(fields, club)
    checks, seen = [], set()
    for phone in fields.contact_phones:
        parsed = _parse_phone(phone.value, region)
        if parsed is None:
            checks.append(Check(check="contact_phone_country", status="not_confirmed", strength="strong", quotes=[phone.quote],
                                note=f"{phone.value} could not be read as a valid phone number."))
            continue
        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        if e164 in seen:
            continue
        seen.add(e164)
        number_region = phonenumbers.region_code_for_number(parsed)
        number_country = _region_names().get(number_region, number_region)
        if region is None:
            checks.append(Check(check="contact_phone_country", status="not_confirmed", strength="strong", quotes=[phone.quote],
                                note=f"{e164} is a phone number from {number_country}. The club's country is unknown, so it was not compared."))
        elif number_region == region:
            checks.append(Check(check="contact_phone_country", status="consistent", strength="strong", quotes=[phone.quote],
                                note=f"{e164} is a phone number from {number_country}, the same country as the club."))
        elif fields.document_type == "agency_letter":
            checks.append(Check(check="contact_phone_country", status="not_confirmed", strength="strong", quotes=[phone.quote],
                                note=f"{e164} is a phone number from {number_country}. The letter is from an agency, so its own "
                                     "number is expected; confirm the offer with the club itself."))
        else:
            checks.append(Check(
                check="contact_phone_country", status="not_confirmed", strength="strong", red_flag=True, quotes=[phone.quote],
                note=f"{e164} is a phone number from {number_country}, but the club is in {country}. A foreign club giving a "
                     f"number from {number_country} as its contact is a warning sign; call the club's official number instead.",
            ))
    return checks


def _parse_phone(value: str, region: Optional[str]) -> Optional[phonenumbers.PhoneNumber]:
    # Numbers without a country code are read as the club's country first, then Kenya.
    for default in ([region] if region else []) + ["KE"]:
        try:
            parsed = phonenumbers.parse(value, default)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(parsed):
            return parsed
    return None


def fee_checks(fields: DocumentFields, has_text: bool) -> List[Check]:
    if not has_text:
        return [Check(check="fee_or_payment_request", status="not_confirmed", strength="strong",
                      note="The document text could not be read, so it is unknown whether it asks for money.")]
    if not fields.fee_requests:
        return [Check(check="fee_or_payment_request", status="consistent", strength="strong",
                      note="The document does not ask the player for money.")]
    checks = []
    for fee in fields.fee_requests:
        amount = " ".join(part for part in (fee.currency, fee.amount) if part) or "money"
        personal = fee.payee_type == "individual" or (fee.payment_method == "mobile_money" and fee.payee_type != "club")
        parts = [f"The document asks the player to pay {amount}" + (f" for {fee.purpose}" if fee.purpose else "") + "."]
        if personal:
            parts.append("The money goes to a personal account or phone number, not to a club.")
        if fee.payment_method in {"money_transfer_service", "crypto"}:
            parts.append("Money-transfer services and crypto payments cannot be reversed.")
        if fee.refundable_claim:
            parts.append("Calling it refundable does not change this.")
        parts.append("Clubs do not charge players for a trial, a contract, registration or a visa.")
        checks.append(Check(check="fee_or_payment_request", status="contradicted", strength="strong", red_flag=True,
                            quotes=[fee.quote], note=" ".join(parts)))
    return checks


# --- Medium checks --------------------------------------------------------------------------


def club_check(fields: DocumentFields, club: Optional[ClubFacts]) -> Check:
    if fields.club_name is None:
        return Check(check="club_and_league", status="not_confirmed", strength="medium",
                     note="No club name was extracted from the document.")
    name = fields.club_name.value
    quotes = _q(fields.club_name, fields.club_country, fields.league)
    if club is None:
        return Check(check="club_and_league", status="not_confirmed", strength="medium", quotes=quotes,
                     note=f"{name} was not found on Wikidata. Small clubs often have little online coverage, "
                          "so this does not mean the club does not exist.")
    sources = [club.source]
    stated_country = fields.club_country.value if fields.club_country else None
    if stated_country and club.country:
        same = (country_code(stated_country) or stated_country.casefold()) == (club.country_iso2 or club.country.casefold())
        if not same:
            return Check(check="club_and_league", status="contradicted", strength="medium", red_flag=True, quotes=quotes, sources=sources,
                         note=f"The document places {name} in {stated_country}, but Wikidata lists {club.name} in {club.country}.")
    stated_league = fields.league.value if fields.league else None
    if stated_league and club.leagues and not _league_matches(stated_league, club.leagues):
        return Check(check="club_and_league", status="not_confirmed", strength="medium", quotes=quotes, sources=sources,
                     note=f"The document says {stated_league}; Wikidata lists {', '.join(club.leagues)}. Clubs move "
                          "between leagues, so check the club's current league.")
    where = ", ".join(part for part in (club.country, ", ".join(club.leagues)) if part)
    return Check(check="club_and_league", status="consistent", strength="medium", quotes=quotes, sources=sources,
                 note=f"{club.name} exists on Wikidata ({where}). This says the club is real, not that it sent the letter.")


def _league_matches(stated: str, leagues: List[str]) -> bool:
    return any(fuzz.token_set_ratio(stated.casefold(), league.casefold()) >= LEAGUE_MATCH_MIN for league in leagues)


@lru_cache(maxsize=1)
def load_windows() -> Tuple[dict, ...]:
    path = ROOT / settings.windows_csv_path
    if not path.is_file():
        return ()
    with path.open(newline="", encoding="utf-8") as handle:
        rows = []
        for row in csv.DictReader(handle):
            try:
                row["opening"] = date.fromisoformat(row["opening_date"])
                row["closing"] = date.fromisoformat(row["closing_date"])
            except (KeyError, ValueError):
                continue
            rows.append(row)
        return tuple(rows)


def transfer_window_check(fields: DocumentFields, club: Optional[ClubFacts], windows: Tuple[dict, ...]) -> Optional[Check]:
    """FIFA RSTP articles 6 and 18, applied only to contract, registration and 'you will play' claims."""
    applies = fields.offer_type in {"contract", "registration"} or fields.document_type in {"contract", "pre_contract"}
    claimed = fields.registration_or_start_date
    if not applies or claimed is None:
        return None
    quotes = _q(claimed)
    when = _iso(claimed)
    if when is None:
        return Check(check="transfer_window", status="not_confirmed", strength="medium", quotes=quotes,
                     note="The registration or start date could not be read as a date.")
    region, country = _club_region(fields, club)
    leagues = ([fields.league.value] if fields.league else []) + (club.leagues if club else [])
    rows = [
        row for row in windows
        if (country_code(row["country"]) or row["country"].casefold()) == (region or (country or "").casefold())
        and _league_matches(row["league"], leagues)
    ]
    if not rows:
        league = leagues[0] if leagues else "the club's league"
        return Check(check="transfer_window", status="not_confirmed", strength="medium", quotes=quotes,
                     note=f"{league} {WINDOW_NOT_IN_TABLE}, so the date was not checked.")
    simulated = any(row.get("simulated", "").lower() == "true" for row in rows)
    sources = sorted({row["source_url"] for row in rows if row.get("source_url")})
    caveat = " (Window dates are simulated test data.)" if simulated else ""
    inside = next((row for row in rows if row["opening"] <= when <= row["closing"]), None)
    if inside:
        return Check(check="transfer_window", status="consistent", strength="medium", quotes=quotes, sources=sources,
                     note=f"{when} is inside the {inside['league']} {inside['window']} window "
                          f"({inside['opening']} to {inside['closing']}).{caveat}")
    previous = max((row for row in rows if row["closing"] < when), key=lambda row: row["closing"], default=None)
    outside = f"{when} is outside the {rows[0]['league']} registration periods."
    status_rule = {
        "under_contract": ("contradicted", True, "The document says the player is under contract, and a player under "
                           "contract can only be registered inside the new club's window. This is inconsistent as "
                           "written. Please confirm with the club."),
        "pre_contract": ("not_confirmed", False, "With a pre-contract, registration normally happens when the next "
                         "window opens. Ask the club for the registration date."),
        "never_professional": ("not_confirmed", False, "The player has not held a professional contract. Associations "
                               "apply the out-of-window exception differently, so this cannot be decided here."),
        "unknown": ("not_confirmed", False, "The document does not say whether the player is under contract. "
                    "Tell us your contract status and when any previous contract ended."),
    }
    if fields.player_contract_status == "contract_ended":
        ended = _iso(fields.previous_contract_end_date)
        quotes += _q(fields.previous_contract_end_date)
        if ended is None or previous is None:
            status, flag, rule = ("not_confirmed", False, "A player whose contract has ended can be registered outside "
                                  "the window only if it ended before the window closed. When did it end?")
        elif ended <= previous["closing"]:
            status, flag, rule = ("consistent", False, f"The previous contract ended on {ended}, before the window "
                                  f"closed on {previous['closing']}, so out-of-window registration may be allowed.")
        else:
            status, flag, rule = ("contradicted", True, f"The previous contract ended on {ended}, after the window "
                                  f"closed on {previous['closing']}, so registration on {when} is not possible as written.")
    else:
        status, flag, rule = status_rule[fields.player_contract_status]
    return Check(check="transfer_window", status=status, strength="medium", red_flag=flag, quotes=quotes,
                 sources=sources, note=f"{outside} {rule}{caveat}")


def date_order_check(fields: DocumentFields) -> Optional[Check]:
    named = [
        ("letter date", fields.letter_date), ("arrival", fields.arrival_date), ("trial start", fields.trial_start_date),
        ("trial end", fields.trial_end_date), ("departure", fields.departure_date),
    ]
    dated = [(label, value, _iso(value)) for label, value in named if _iso(value)]
    if len(dated) < 2:
        return None
    problems, quotes = [], []
    # Each event must not come before an earlier one in the list (letter, arrival, trial, departure).
    for i, (label_a, value_a, day_a) in enumerate(dated):
        for label_b, value_b, day_b in dated[i + 1:]:
            if day_b < day_a and not (label_a == "letter date" and label_b == "letter date"):
                problems.append(f"{label_b} ({day_b}) is before {label_a} ({day_a})")
                quotes += [value_a.quote, value_b.quote]
    if problems:
        return Check(check="trial_and_travel_dates", status="contradicted", strength="medium", red_flag=True,
                     quotes=list(dict.fromkeys(quotes)), note="The dates conflict: " + "; ".join(problems) + ".")
    return Check(check="trial_and_travel_dates", status="consistent", strength="medium", quotes=[v.quote for _, v, _ in dated],
                 note="The letter, travel and trial dates are in a possible order.")


def detail_checks(fields: DocumentFields) -> List[Check]:
    return [
        Check(check="contract_or_trial_details", status="not_confirmed", strength="medium", red_flag=True,
              quotes=[item.quote], note=item.description)
        for item in fields.inconsistencies
    ]


# --- Weak checks ----------------------------------------------------------------------------


def _pdf_date(value: str) -> Optional[datetime]:
    match = re.match(r"D:(\d{14})", value or "")
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def metadata_check(metadata: dict, today: date) -> Check:
    if not metadata:
        return Check(check="file_metadata", status="not_confirmed", strength="weak",
                     note="The file carries no metadata. This is normal for screenshots and forwarded files.")
    software = " / ".join(v for k, v in metadata.items() if k.lower() in {"creator", "producer", "software"} and v)
    created = _pdf_date(metadata.get("creationDate", ""))
    described = f"Made with {software or 'unknown software'}" + (f", created {created.date()}" if created else "") + "."
    design_tool = bool(DESIGN_TOOLS.search(software))
    recent = bool(created and today - created.date() <= timedelta(days=3))
    note = described
    if design_tool:
        note += " Club letters are normally produced by office software; a design tool is a weak warning sign."
    if design_tool and recent:
        note += " The file was also created in the last few days."
    note += " Metadata is weak evidence either way."
    return Check(check="file_metadata", status="not_confirmed", strength="weak", red_flag=design_tool, note=note)


# --- For later steps ------------------------------------------------------------------------


def pending_checks(fields: DocumentFields, club: Optional[ClubFacts], window: Optional[Check]) -> List[str]:
    pending = []
    if fields.signatory_name:
        role = f" as {fields.signatory_role.value}" if fields.signatory_role else ""
        pending.append(f"Is {fields.signatory_name.value} listed{role} on the club's official site? (web-checking agent)")
    if fields.club_name:
        pending.append("Find the club's official contact details to draft a confirmation message (web-checking agent)")
        if club is None:
            pending.append(f"Search the web for {fields.club_name.value}'s official website (web-checking agent)")
    if window_missing([window] if window else []):
        pending.append("Look up this league's registration dates with a source link (web-checking agent)")
    return pending


def window_missing(checks: List[Check]) -> bool:
    """True when the club's league has no row in the window table (the web agent looks it up)."""
    return any(c.check == "transfer_window" and WINDOW_NOT_IN_TABLE in c.note for c in checks)


def linking_keys(doc: LoadedDocument, fields: DocumentFields) -> LinkingKeys:
    emails = {fields.sender_email.value.lower()} if fields.sender_email else set()
    phones = set()
    for phone in fields.contact_phones + ([fields.agent_phone] if fields.agent_phone else []):
        parsed = _parse_phone(phone.value, None)
        if parsed:
            phones.add(phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164))
    accounts = {re.sub(r"\D", "", fee.account_or_number) for fee in fields.fee_requests if fee.account_or_number}
    return LinkingKeys(
        file_sha256=hashlib.sha256(doc.data).hexdigest(),
        page_phashes=doc.page_phashes,
        email_hashes=sorted(hash_lookup_value(f"email:{v}") for v in emails),
        phone_hashes=sorted(hash_lookup_value(f"phone:{v}") for v in phones),
        payment_account_hashes=sorted(hash_lookup_value(f"account:{v}") for v in accounts if v),
    )
