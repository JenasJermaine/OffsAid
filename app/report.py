"""The WhatsApp replies. Filled from the check results, never free-form model text."""

from __future__ import annotations

from typing import List, Optional

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
WORDING_HEADING = "*Wording patterns in the agent's messages*"
WORDING_UNAVAILABLE = "We could not read the chat in this image, so this check did not run."
WORDING_NO_AGENT_TEXT = "No agent text found in this screenshot."
WORDING_NONE_FOUND = (
    "None of the known wording patterns were found in the agent's messages. "
    "That is not a judgement that the offer is genuine."
)
WORDING_RULES_ONLY = (
    "The wording model is not loaded. Only the fee-wording rule ran, so other patterns are not covered here."
)
WORDING_PATTERNS = (
    "These lines are wording patterns. They are not a conclusion about the person who wrote them."
)

LINKING_HEADING = "*Comparison with earlier cases*"
LINKING_UNAVAILABLE = "Comparison with earlier cases was not available for this check."
LINKING_RATE_LIMITED = (
    "Comparison with earlier cases did not run, because many checks were sent from this number "
    "in a short time. Please try again later."
)
LINKING_SIMULATED = "Some of these earlier cases are simulated demo cases, not real reports."
LINKING_ESCALATED = "This case has been added to KEFWA's review list."
LINKING_NOT_PROOF = (
    "A match means these details were seen before. It is not proof about the person who sent this offer."
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
        parts.append("*Next:* we are now checking the club's own website. The result comes in a second message, "
                     "usually within 1 to 3 minutes. Please wait for it before you run another check.")
    parts.append(FOOTER)
    return "\n\n".join(parts)


def wording_report(result: dict) -> str:
    """The wording section of the evidence report.

    Quotes and plain-language meanings only. Scores stay out of this text: a rule
    flag's 0.5 is a placeholder, and model scores are not a measure of fraud.
    An empty flag list is never written up as the person being genuine.
    """
    parts = [WORDING_HEADING]
    if result.get("note"):
        parts.append(result["note"])
    if result.get("unavailable"):
        parts.append(WORDING_UNAVAILABLE)
        return "\n\n".join(parts)
    if result.get("messages_checked", 0) == 0:
        parts.append(WORDING_NO_AGENT_TEXT)
        return "\n\n".join(parts)
    if result.get("mode") == "rules_only":
        parts.append(WORDING_RULES_ONLY)
    flags = result.get("flags") or []
    if not flags:
        parts.append(WORDING_NONE_FOUND)
        return "\n\n".join(parts)
    lines = []
    for flag in flags:
        text = flag.get("quote") or ""
        shown = text[:MAX_QUOTE] + ("..." if len(text) > MAX_QUOTE else "")
        lines.append(f'- "{shown}"\n  {flag.get("meaning", "")}')
    parts.append("\n".join(lines))
    parts.append(WORDING_PATTERNS)
    return "\n\n".join(parts)


def linking_report(view: Optional[dict]) -> str:
    """The cross-case linking section, from case_linking.player_view (plus 'simulated').

    Only the summary sentences: no case ids, dates, other players' details, raw identifiers
    or percentages. None means linking was not available; the rest of the report still goes out.
    """
    parts = [LINKING_HEADING]
    if view is None:
        parts.append(LINKING_UNAVAILABLE)
        return "\n\n".join(parts)
    if view.get("rate_limited"):
        parts.append(LINKING_RATE_LIMITED)
        return "\n\n".join(parts)
    links = view.get("links") or []
    if not links:
        parts.append(view.get("no_match_note") or LINKING_UNAVAILABLE)
        return "\n\n".join(parts)
    ordered = [l for l in links if l["flag"]] + [l for l in links if not l["flag"]]
    parts.append("\n".join(f"- {l['summary']}" for l in ordered))
    if view.get("simulated"):
        parts.append(LINKING_SIMULATED)
    if view.get("escalated"):
        parts.append(LINKING_ESCALATED)
    parts.append(LINKING_NOT_PROOF)
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
