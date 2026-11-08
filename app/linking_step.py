"""Cross-case linking in the WhatsApp pipeline (docs/CROSS_CASE_LINKING_GUIDE.md, section 5).

    extraction -> document check + wording check -> CROSS-CASE LINKING -> evidence report (+ KEFWA queue)

whatsapp_webhook._send_offer calls run_linking() after the document and wording checks and
before the first report is built. The web-checking agent runs after that report and is not
an input here. Linking needs Postgres (pg_trgm); on SQLite, or when anything fails, the report
says the comparison was not available and the rest of the report is sent as usual.

Never logged here: message text, identifiers, hashes or case ids.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from typing import Iterator, Optional

from app import case_linking as cl
from app.config import settings
from app.db import engine, is_postgres
from app.document_schemas import DocumentCheckResponse
from app.hashing import hash_lookup_value

log = logging.getLogger(__name__)

RATE_WINDOW_SECONDS = 3600


class LinkingUnavailable(RuntimeError):
    pass


# --- connection and key ------------------------------------------------------------------


def hash_key() -> bytes:
    """OFFSAID_HASH_KEY from the environment or .env. Same rule as case_linking.get_hash_key."""
    key = settings.offsaid_hash_key
    if len(key) < 32:
        raise LinkingUnavailable("OFFSAID_HASH_KEY must be set to a secret of at least 32 characters.")
    return key.encode()


def psycopg_dsn(url: Optional[str] = None) -> str:
    """DATABASE_URL in SQLAlchemy form (postgresql+psycopg://) -> a plain psycopg DSN."""
    url = url or settings.database_url
    return re.sub(r"^postgres(?:ql)?(?:\+\w+)?://", "postgresql://", url)


@contextmanager
def linking_connection() -> Iterator:
    """A psycopg 3 connection from the app's SQLAlchemy pool, committed at the end (one
    transaction per case) and rolled back on error. case_linking uses it directly."""
    if not is_postgres():
        raise LinkingUnavailable("Cross-case linking needs Postgres; DATABASE_URL is not Postgres.")
    raw = engine.raw_connection()
    try:
        yield raw.driver_connection
        raw.commit()
    except BaseException:
        raw.rollback()
        raise
    finally:
        raw.close()


# --- rate limit --------------------------------------------------------------------------


class RateLimiter:
    """Linking runs per WhatsApp user in a sliding window. In memory, per process (run one
    worker, as the scam-language model already requires); it resets when the app restarts."""

    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, user: str, limit: int, window: float = RATE_WINDOW_SECONDS, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        key = hash_lookup_value(f"linking:{user}")
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True


limiter = RateLimiter()


# --- pipeline outputs -> CaseInput -------------------------------------------------------


def _value(quoted) -> Optional[str]:
    return quoted.value if quoted is not None and quoted.value else None


def build_case_input(
    result: DocumentCheckResponse,
    wording: Optional[dict] = None,
    letter_phash: Optional[int] = None,
    external_ref: Optional[str] = None,
    player_number: Optional[str] = None,
) -> cl.CaseInput:
    """Map the document check and the wording check onto CaseInput (guide, step 3).

    Only the other party's details are used: contact and agent numbers, payment accounts and
    the sender email. The player's own WhatsApp number is dropped if the document repeats it.
    Raw values go in; case_linking normalises and hashes them and stores no raw value.
    """
    f = result.fields
    raw: list[dict] = []
    for phone in f.contact_phones + ([f.agent_phone] if f.agent_phone else []):
        if phone.value:
            raw.append({"kind": "phone", "value": phone.value, "role": "agent_contact"})
    for fee in f.fee_requests:
        if fee.account_or_number:
            raw.append({"kind": "account", "value": fee.account_or_number, "role": "payment_to"})
    if _value(f.sender_email):
        raw.append({"kind": "email", "value": f.sender_email.value, "role": "sender"})

    player = cl.canonical_identifier("phone", player_number) if player_number else None
    identifiers = [i for i in raw if player is None or cl.canonical_identifier(i["kind"], i["value"]) != player]

    is_letter = f.document_type != "chat_screenshot" and bool(f.transcribed_text.strip())
    names = [n for n in (_value(f.signatory_name), _value(f.sender_name), _value(f.agent_name)) if n]

    agent_messages = (wording or {}).get("agent_messages") or []
    wording_text = "\n".join(m["text"] for m in agent_messages if m.get("text")) or None
    labels = sorted({flag["label"] for flag in (wording or {}).get("flags", [])})

    summary = {
        "club": _value(f.club_name),
        "document_type": f.document_type,
        "offer_type": f.offer_type,
        "fees": [{"amount": fee.amount, "currency": fee.currency, "purpose": fee.purpose} for fee in f.fee_requests],
        "document_checks": result.summary,
        "red_flag_checks": sorted({c.check for c in result.checks if c.red_flag}),
        "extraction_method": result.extraction_method,
        "wording_labels": labels,
    }
    return cl.CaseInput(
        source="live",
        external_ref=external_ref or None,
        claimed_club=_value(f.club_name),
        claimed_agent=_value(f.agent_name) or _value(f.signatory_name) or _value(f.sender_name),
        identifiers=identifiers,
        letter_text=f.transcribed_text if is_letter else None,
        letter_phash=letter_phash if is_letter else None,
        wording_text=wording_text,
        wording_labels=labels,
        mask_names=names,
        summary=summary,
    )


def first_page_phash(data: bytes, mime_type: str) -> Optional[int]:
    """Perceptual hash of page 1, computed in memory. The file is not kept."""
    from app.document_extraction import first_page_image

    try:
        return cl.image_phash(first_page_image(data, mime_type))
    except Exception as exc:
        log.warning("Letter fingerprint failed (%s); linking runs without it", type(exc).__name__)
        return None


# --- the step ----------------------------------------------------------------------------


def queue_for_review(conn, linked: dict) -> None:
    """Add an escalated case to the KEFWA review queue with its reasons and the matching case ids."""
    from psycopg.types.json import Jsonb

    case_ids = sorted({cid for link in linked["links"] for cid in link["case_ids"]})
    conn.execute(
        "insert into review_queue (case_id, reasons, linked_case_ids) values (%s, %s, %s) "
        "on conflict (case_id) do nothing",
        (linked["case_id"], Jsonb(linked["escalation_reasons"]), case_ids),
    )


def _involves_simulated(conn, linked: dict) -> bool:
    case_ids = [cid for link in linked["links"] for cid in link["case_ids"]]
    if not case_ids:
        return False
    row = conn.execute(
        "select exists (select 1 from cases where id = any(%s) and source = 'simulated')", (case_ids,)
    ).fetchone()
    return bool(row[0])


def link_case(conn, case: cl.CaseInput, key: bytes) -> dict:
    """process_case, then queue an escalation. Returns the player-safe view plus 'simulated'."""
    linked = cl.process_case(conn, case, key)
    if linked["escalate"]:
        queue_for_review(conn, linked)
    view = cl.player_view(linked)
    view["simulated"] = _involves_simulated(conn, linked)
    # For a KEFWA referral from the chat summary only. Never printed: report.linking_report shows sentences.
    view["internal_case_id"] = linked["case_id"]
    log.info("cross-case linking: links=%d escalated=%s", len(view["links"]), view["escalated"])
    return view


def run_linking(
    result: DocumentCheckResponse,
    wording: Optional[dict],
    data: bytes,
    mime_type: str,
    external_ref: Optional[str],
    player_number: str,
) -> Optional[dict]:
    """The pipeline call. Returns the player view, {"rate_limited": True}, or None when linking
    was not available. Never raises: a linking failure must not stop the report."""
    try:
        if not is_postgres():
            log.info("Cross-case linking skipped: DATABASE_URL is not Postgres")
            return None
        key = hash_key()
        if not limiter.allow(player_number, settings.linking_max_per_hour):
            log.info("Cross-case linking rate-limited for one user")
            return {"rate_limited": True}
        case = build_case_input(result, wording, first_page_phash(data, mime_type), external_ref, player_number)
        with linking_connection() as conn:
            return link_case(conn, case, key)
    except Exception as exc:
        # Type only: driver errors can carry parameter values in their message.
        log.error("Cross-case linking failed (%s); report sent without it", type(exc).__name__)
        return None
