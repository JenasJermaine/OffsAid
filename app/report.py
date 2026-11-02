"""The WhatsApp replies. Filled from the check results, never free-form model text."""

from __future__ import annotations

from typing import List

from app.document_schemas import Check, DocumentCheckResponse
from app.web_agent import WebCheckResult

ACK = "Thanks, we received your document. We are checking it now; this can take a minute."
HELP = (
    "Hello, this is OffsAid. Forward us a recruitment offer as a PDF or a photo/screenshot "
    "(PNG or JPEG) and we will check what it claims against public sources.\n\n"
    "We report what we found and what we could not confirm. We do not say whether an offer "
    "is real or fake."
)
UNSUPPORTED = "Sorry, we can only read PDF files and PNG or JPEG images. Please send the offer in one of those formats."
TOO_LARGE = "Sorry, that file is too large. Please send a file under 20 MB, or a screenshot of each page."
FAILED = "Sorry, something went wrong while reading your document. Please try again, or contact KEFWA directly."
FOOTER = (
    "'Not confirmed' does not mean fake. The safest check is to contact the club using the details "
    "on its official website. If you are unsure, contact KEFWA before you pay or travel."
)

LABELS = {
    "sender_email_domain": "Sender email",
    "sender_domain_age": "Sender's domain",
    "contact_phone_country": "Contact phone",
    "club_and_league": "Club and league",
    "fee_or_payment_request": "Payment request",
    "transfer_window": "Registration date",
    "trial_and_travel_dates": "Dates",
    "contract_or_trial_details": "Details that conflict",
    "file_metadata": "File",
}
WEB_STATUS = {"confirmed": "Confirmed", "not_found": "Not found", "contradicted": "Contradicted"}
MAX_QUOTE = 160


def _quote(check: Check) -> str:
    if not check.quotes:
        return ""
    text = check.quotes[0]
    return f'\n  "{text[:MAX_QUOTE]}{"..." if len(text) > MAX_QUOTE else ""}"'


def _line(check: Check) -> str:
    label = LABELS.get(check.check, check.check)
    if check.check == "contract_or_trial_details":
        # The description is model-written; show only the quoted line from the document.
        return f"- {label}: these details in the document conflict with each other.{_quote(check)}"
    return f"- {label}: {check.note}{_quote(check)}"


def document_report(response: DocumentCheckResponse, web_check_running: bool = False) -> str:
    contradicted = [c for c in response.checks if c.status == "contradicted"]
    warnings = [c for c in response.checks if c.red_flag and c.status != "contradicted"]
    consistent = [c for c in response.checks if c.status == "consistent"]
    unconfirmed = [c for c in response.checks if c.status == "not_confirmed" and not c.red_flag and c.check != "file_metadata"]

    parts: List[str] = ["*OffsAid document check*\nWe checked what this document claims against public sources."]
    if response.extraction_method == "patterns_only":
        parts.append("We could only read part of this document, so some checks could not run.")
    if contradicted:
        parts.append("*Inconsistent as written*\n" + "\n".join(_line(c) for c in contradicted))
    if warnings:
        parts.append("*Warning signs*\n" + "\n".join(_line(c) for c in warnings))
    if consistent:
        parts.append("*Consistent with public sources*\n" + "\n".join(f"- {LABELS.get(c.check, c.check)}: {c.note}" for c in consistent))
    if unconfirmed:
        parts.append("*Not confirmed*\n" + "\n".join(f"- {LABELS.get(c.check, c.check)}: {c.note}" for c in unconfirmed))
    if web_check_running:
        parts.append("We are now checking the club's own website. We will send a second message when that is done.")
    parts.append(FOOTER)
    return "\n\n".join(parts)


def web_report(result: WebCheckResult) -> str:
    club = result.club or "the club"
    parts = [f"*OffsAid web check: {club}*"]
    if result.status in {"skipped", "failed"}:
        parts.append("We could not check the club's website this time. " + " ".join(result.notes))
        parts.append(FOOTER)
        return "\n\n".join(parts)
    lines = []
    for row in result.rows:
        source = f"\n  Source: {row.source_url}" if row.status != "not_found" and row.source_url else ""
        lines.append(f"- {row.claim} {WEB_STATUS[row.status]}.{source}")
    parts.append("\n".join(lines))
    if result.status == "incomplete":
        parts.append("The check stopped before it finished, so some claims were not looked at.")
    if result.official_contact:
        contact = result.official_contact
        details = ", ".join(v for v in (contact.email, contact.phone) if v)
        parts.append(f"*The club's official contact* (from {contact.contact_page_url}): {details}")
    if result.draft_message:
        parts.append("You can send this message to the club to ask them directly:\n\n" + result.draft_message)
    parts.append("'Not found' does not mean fake: small clubs often have little online coverage.\n\n" + FOOTER)
    return "\n\n".join(parts)
