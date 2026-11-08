"""The OffsAid chat: a menu, three checks the player can run separately, a summary, and KEFWA referral.

    @agent-check     Name / Licence number / Phone / Country  -> licensed-agent lookup (Step 1)
    @document-check  a PDF or photo of a document            -> claim checks, cross-case linking, web check
    @message-check   a chat screenshot, or pasted messages     -> wording patterns in the agent's messages
    done             summary of everything checked in this chat; referral to KEFWA when a check raised concerns

Transport-agnostic: the caller passes `send(text)`. The web demo (app/demo_chat.py) uses it today; the
WhatsApp/Twilio webhooks can call handle() the same way, with the player's number as `contact`.

Team decision (2026-11-08): when a case is referred, the player's phone number is shared with KEFWA so KEFWA
can contact them. The welcome message says so before any check, and the referral message repeats it.
Nothing here logs message text, numbers or names.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

import phonenumbers

from app import report
from app.agent_check import run_agent_check
from app.db import SessionLocal
from app.linking_step import linking_connection
from app.models import LicensedAgent
from app.schemas import DISCLAIMER, AgentCheckRequest
from app.scam_runtime import get_scam_checker

log = logging.getLogger(__name__)

Send = Callable[[str], None]

# --- what the player reads ---------------------------------------------------------------

WELCOME = (
    "*Welcome to OffsAid*\n"
    "We help footballers check a recruitment offer before paying or travelling. We show you what we found. "
    "We never say an offer is real or fake.\n\n"
    "*Choose a check* by sending its name (or its number):\n\n"
    "1. *@agent-check*\n"
    "Is the agent on the licensed agents list? You send the agent's name, licence number or phone number.\n\n"
    "2. *@document-check*\n"
    "Check a document you were sent: an offer letter, trial invitation, contract, visa letter or payment request.\n\n"
    "3. *@message-check*\n"
    "Check the agent's chat messages for warning signs. You send a screenshot of the chat.\n\n"
    "You can run one check or all three, in any order. When you are finished, send *done* for a summary "
    "of everything we found. Send *menu* at any time to see this again.\n\n"
    "_Privacy: if your case needs a closer look, we send it to KEFWA (Kenya Footballers Welfare Association) "
    "for review. KEFWA then receives your phone number and our findings so they can contact you._"
)

AGENT_PROMPT = (
    "*Agent check*\n"
    "We look the agent up on the licensed agents list.\n\n"
    "Copy the next message, fill in what you know, and send it back.\n"
    "- *Name*: the agent's full name, as they gave it to you\n"
    "- *Licence number*: optional, if the agent gave you one\n"
    "- *Phone*: optional, the number the agent contacts you from\n"
    "- *Country*: optional, where the agent says they are licensed\n\n"
    "You need at least one of name, licence number or phone. The more you give, the better the check.\n\n"
    "_Example:_\n"
    "Name: James Otieno\n"
    "Licence number: KEFWA-SIM-002\n"
    "Phone: 0700 000 002\n"
    "Country: Kenya"
)
AGENT_TEMPLATE = "Name: \nLicence number: \nPhone: \nCountry: "
AGENT_NEED_ONE = (
    "We need at least the agent's *name*, *licence number* or *phone*. Please send it like this:\n\n"
    "Name: James Otieno\nPhone: 0700 000 002"
)

DOCUMENT_PROMPT = (
    "*Document check*\n"
    "Send the document as a *PDF*, or a clear *photo or screenshot* (PNG or JPEG), up to 20 MB.\n\n"
    "Documents we can check, for example:\n"
    "- A *trial or academy invitation* from a club\n"
    "- An *offer letter* or *letter of intent* (\"we want to sign you\")\n"
    "- A *contract* or *pre-contract*\n"
    "- A letter from an *agent or agency* saying they represent you\n"
    "- A *visa, flight or travel* letter\n"
    "- A *payment request* or invoice: registration, visa, \"clearance\" or medical fees\n\n"
    "Tips: photograph the whole page, flat and in good light. Send one document at a time. "
    "For a chat with the agent, use *@message-check* instead."
)
DOCUMENT_WAITING = (
    "Please send the document as a PDF, photo or screenshot. "
    "Or send *menu* to choose another check."
)

MESSAGE_PROMPT = (
    "*Message check*\n"
    "Send a *screenshot* of your chat with the agent (WhatsApp, Telegram, SMS, Facebook...). "
    "Make sure the agent's messages can be read. We only check what the agent wrote, not your messages.\n\n"
    "You can also *paste* the agent's messages here as text.\n\n"
    "Tip: you can send a screenshot with *@message-check* written as its caption."
)
SCREENSHOT_ACK = "Thanks, we received your screenshot. We are reading the agent's messages now; this can take a minute."

NEXT = (
    "Do you have anything else to check?\n"
    "Send *@agent-check*, *@document-check* or *@message-check*, or send *done* for a summary of what we found."
)
NOTHING_YET = "You have not run any checks in this chat yet.\n\n" + WELCOME

GUIDANCE = (
    "*What you can do now*\n"
    "- Do not pay any fee, and do not send your passport or ID, until you have confirmed the offer with the club "
    "directly, using the contact details on the club's official website.\n"
    "- Clubs do not charge players for a trial, a contract, registration or a visa.\n"
    "- If you are unsure, talk to KEFWA, your coach or your family before you pay or travel."
)
NO_CONCERNS = (
    "None of the checks above found a warning sign. That does not prove the offer is genuine: "
    "our checks cannot see everything. If something still feels wrong, contact KEFWA directly."
)
ASK_CONTACT = (
    "We would like KEFWA to review your case, because:\n{reasons}\n\n"
    "KEFWA will receive your phone number and the findings above so they can contact you.\n\n"
    "Reply with the phone number KEFWA can reach you on (for example 0712 345 678), "
    "or send *skip* to send the case without a number."
)
BAD_CONTACT = "That does not look like a phone number. Please send it like 0712 345 678, or send *skip*."
REFERRED = (
    "*Sent to KEFWA for review*\n"
    "We have sent your case to KEFWA (Kenya Footballers Welfare Association), because:\n{reasons}\n\n"
    "{contact_line}\n\n"
    "A person at KEFWA will look at it, so a reply can take some time. Until then, do not pay any money "
    "and do not send your passport or ID to the agent."
)
REFERRAL_FAILED = (
    "We could not send your case to KEFWA automatically this time. Please contact KEFWA directly and "
    "show them the findings above."
)
START_AGAIN = "Send *menu* to check another offer."

STATUS_LINE = {
    "found": "*On the licensed agents list*",
    "possible_match": "*Possible match: some details differ*",
    "not_found": "*Not found on the licensed agents list*",
}
NOT_FOUND_ADVICE = (
    "This does not prove the person is not an agent: our list may be incomplete. Ask the agent for their "
    "licence number, then confirm it with KEFWA or on FIFA's public list of licensed agents. Be careful with "
    "anyone who asks you for money and cannot show a licence."
)
POSSIBLE_ADVICE = "Compare the record below with what the agent told you. If the details do not match, ask KEFWA."

LABEL_SHORT = {
    "advance_fee_request": "asks for a fee before any signed contract",
    "personal_account_payment": "asks for payment to a personal number or account",
    "urgency_pressure": "pressures you to act or pay quickly",
    "secrecy": "asks you to keep it secret",
    "guaranteed_outcome": "promises a trial, contract or move as certain",
    "visa_travel_arranged": "claims a visa or travel is already arranged",
    "off_platform_move": "asks to move the chat to another app",
}
DOC_TYPE = {
    "offer_letter": "offer letter", "trial_invitation": "trial invitation", "contract": "contract",
    "pre_contract": "pre-contract", "agency_letter": "agency letter", "chat_screenshot": "chat screenshot",
    "visa_or_travel": "visa or travel document", "other": "document",
}
LINK_SUBJECT = {"phone": "phone number", "account": "payment account", "email": "email address",
                "domain": "sender domain", "letter_template": "letter layout", "wording": "wording"}

# --- commands ----------------------------------------------------------------------------

ALIASES = {
    **dict.fromkeys(["agentcheck", "agent", "checkagent", "1"], "agent"),
    **dict.fromkeys(["documentcheck", "document", "doccheck", "doc", "checkdocument", "2"], "document"),
    **dict.fromkeys(["messagecheck", "message", "messages", "chatcheck", "chat", "checkmessage", "3"], "message"),
    **dict.fromkeys(["done", "summary", "finish", "finished", "thatsall", "thatsit", "nothingelse", "no", "nope",
                     "imdone", "end"], "done"),
    **dict.fromkeys(["menu", "help", "hi", "hello", "hey", "start", "habari", "mambo", "0"], "menu"),
}
CHECKS = ("agent", "document", "message")
# Casual words that only count as commands outside a check, so a pasted chat that starts with "Hello",
# or "No" as an answer, is not mistaken for one. Written with @, or as menu/done, they always count.
WEAK = {"hi", "hello", "hey", "habari", "mambo", "start", "help", "no", "nope", "end", "agent", "document", "doc",
        "message", "messages", "chat", "0", "1", "2", "3"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def parse_command(text: str, strict: bool = False) -> tuple[Optional[str], str]:
    """-> (command or None, the rest of the message). Accepts "@agent-check", "agent check", "1"...
    A command can be followed by details on the same or the next lines. strict: ignore WEAK words."""
    t = (text or "").strip()
    if not t:
        return None, ""
    first, _, rest = t.partition("\n")
    key = _norm(first)
    if key in ALIASES and not (strict and key in WEAK and not first.lstrip().startswith("@")):
        return ALIASES[key], rest.strip()
    m = re.match(r"^@\s*([a-z]+(?:[\s_-]*check)?)\b[\s:,-]*(.*)$", first, re.I)
    if m and _norm(m.group(1)) in ALIASES:
        return ALIASES[_norm(m.group(1))], (m.group(2) + "\n" + rest).strip()
    return None, t


AGENT_KEYS = {
    "name": "name", "agentname": "name", "agent": "name", "fullname": "name",
    "licence": "licence_number", "license": "licence_number", "licencenumber": "licence_number",
    "licensenumber": "licence_number", "licenceno": "licence_number", "licenseno": "licence_number", "lic": "licence_number",
    "phone": "phone", "phonenumber": "phone", "number": "phone", "whatsapp": "phone", "tel": "phone", "mobile": "phone",
    "country": "claimed_country", "licensedin": "claimed_country", "licencedin": "claimed_country",
}


def parse_agent_details(text: str) -> dict:
    """"Name: X" lines -> AgentCheckRequest fields. A single unlabelled line is read as a phone, a licence
    number or a name, by its shape."""
    out: dict = {}
    for line in (text or "").splitlines():
        key, sep, value = line.partition(":")
        if sep and _norm(key) in AGENT_KEYS and value.strip():
            out[AGENT_KEYS[_norm(key)]] = value.strip()
    if out:
        return out
    lines = [l.strip() for l in (text or "").splitlines() if l.strip() and ":" not in l]
    if len(lines) == 1:
        v = lines[0]
        if len(re.sub(r"\D", "", v)) >= 9 and not re.search(r"[A-Za-z]{2}", v):
            return {"phone": v}
        if re.search(r"\d", v) and re.search(r"[A-Za-z]", v) and " " not in v.strip():
            return {"licence_number": v}
        return {"name": v}
    return {}


# --- state -------------------------------------------------------------------------------


@dataclass
class Conversation:
    sender: str                                  # transport id: WhatsApp number, or "demo-<chat id>" in the web demo
    contact: Optional[str] = None                # the player's phone number, when the transport knows it
    mode: Optional[str] = None                   # agent | document | message | contact
    findings: list = field(default_factory=list)
    pending: Optional[dict] = None               # a referral waiting for the player's phone number
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


def handle(conv: Conversation, text: str, file: Optional[tuple[bytes, str]], send: Send, message_id: str = "") -> None:
    """One incoming message: text, and optionally a file as (bytes, mime). Messages of one chat run in order."""
    with conv.lock:
        try:
            _handle(conv, text or "", file, send, message_id)
        except Exception:
            log.exception("Chat step failed")
            send(report.FAILED)


def _handle(conv: Conversation, text: str, file, send: Send, message_id: str) -> None:
    command, payload = parse_command(text, strict=conv.mode in ("agent", "message", "contact"))

    if conv.mode == "contact" and file is None and command not in ("menu",):
        _receive_contact(conv, text, send)
        return
    if command == "menu":
        conv.mode, conv.pending = None, None
        send(WELCOME)
        return
    if command == "done":
        _summary(conv, send)
        return
    if command in CHECKS:
        conv.mode = command
        if file is not None:
            _run_file(conv, command, file, send, message_id)
        elif command == "agent" and payload:
            _agent(conv, payload, send)
        elif command == "message" and payload:
            _message_text(conv, payload, send)
        elif command == "agent":
            send(AGENT_PROMPT)
            send(AGENT_TEMPLATE)
        elif command == "document":
            send(DOCUMENT_PROMPT)
        else:
            send(MESSAGE_PROMPT)
        return

    if file is not None:
        _run_file(conv, conv.mode if conv.mode in ("document", "message") else None, file, send, message_id)
    elif conv.mode == "agent":
        _agent(conv, text, send)
    elif conv.mode == "message":
        _message_text(conv, text, send)
    elif conv.mode == "document":
        send(DOCUMENT_WAITING)
    else:
        send(WELCOME)


# --- the three checks --------------------------------------------------------------------


def _agent(conv: Conversation, text: str, send: Send) -> None:
    details = parse_agent_details(text)
    if not any(details.get(k) for k in ("name", "licence_number", "phone")):
        send(AGENT_NEED_ONE)
        return
    db = SessionLocal()
    try:
        result = run_agent_check(db, AgentCheckRequest(**details))
        simulated = any("simulated" in (s or "") for (s,) in db.query(LicensedAgent.source).distinct())
    finally:
        db.close()

    asked = " · ".join(f"{label} {details[k]}" for k, label in
                       (("name", ""), ("licence_number", "licence"), ("phone", "phone"), ("claimed_country", "in"))
                       if details.get(k)).strip()
    parts = ["*Agent check result*", f"You asked about: {asked}", STATUS_LINE[result.status], result.message]
    if result.match:
        m = result.match
        parts.append(f"Record on the list: {m.name}, {m.country}, licence {m.licence_number} ({m.licence_status}).")
    if result.status == "not_found":
        parts.append(NOT_FOUND_ADVICE)
    elif result.status == "possible_match":
        parts.append(POSSIBLE_ADVICE)
    parts.append(DISCLAIMER)
    if simulated:
        parts.append("_This prototype uses a simulated list of licensed agents._")
    send("\n\n".join(parts))

    who = details.get("name") or details.get("licence_number") or details.get("phone")
    status = {"found": "on the licensed agents list", "possible_match": "a possible match: some details differ",
              "not_found": "not found on the licensed agents list"}[result.status]
    concern = {"not_found": "the agent was not found on the licensed agents list",
               "possible_match": "the agent's details only partly match the licensed agents list"}.get(result.status)
    conv.findings.append({"kind": "agent", "line": f"*Agent check* ({who}): {status}.", "concerns": [concern] if concern else []})
    conv.mode = None
    send(NEXT)


def _run_file(conv: Conversation, mode: Optional[str], file: tuple[bytes, str], send: Send, message_id: str) -> None:
    data, mime = file
    if mode == "message":
        _message_screenshot(conv, data, mime, send)
    else:
        _document(conv, data, mime, send, message_id, wording=mode is None)


def _document(conv: Conversation, data: bytes, mime: str, send: Send, message_id: str, wording: bool) -> None:
    from app.whatsapp_webhook import _send_offer

    send(report.ACK)
    outcome = _send_offer(conv.sender, data, mime, lambda _to, body: send(body), message_id, wording_for_images=wording)
    if outcome is None:
        send(DOCUMENT_WAITING)
        return
    conv.findings.append(_document_finding(outcome))
    if outcome.get("wording"):
        conv.findings.append(_wording_finding(outcome["wording"]))
    conv.mode = None
    send(NEXT)


def _document_finding(outcome: dict) -> dict:
    result, linked, web = outcome["document"], outcome.get("linking"), outcome.get("web")
    f = result.fields
    club = f.club_name.value if f.club_name else None
    title = DOC_TYPE.get(f.document_type, "document") + (f" from {club}" if club else "")
    contradicted = [c for c in result.checks if c.status == "contradicted"]
    warnings = [c for c in result.checks if c.red_flag and c.status != "contradicted"]
    unconfirmed = [c for c in result.checks if c.status == "not_confirmed" and not c.red_flag and c.check != "file_metadata"]
    bits = [f"{len(contradicted)} inconsistent as written",
            f"{len(warnings)} warning sign{'' if len(warnings) == 1 else 's'}",
            f"{len(unconfirmed)} not confirmed"]
    if any(c.check == "fee_or_payment_request" and (c.red_flag or c.status == "contradicted") for c in result.checks):
        bits.append("asks you to pay money")
    lines = [f"*Document check* ({title}): " + ", ".join(bits) + "."]
    concerns = []
    if contradicted:
        concerns.append("the document has details that are inconsistent as written")
    if warnings:
        concerns.append("the document shows warning signs")

    case_id = None
    if linked and linked.get("links"):
        case_id = linked.get("internal_case_id")
        flagged = [LINK_SUBJECT.get(l["type"], l["type"]) for l in linked["links"] if l["flag"]]
        if flagged:
            lines.append("Seen before in earlier cases: " + ", ".join(flagged) + ".")
        else:
            lines.append("Some details were seen before with the same club and agent (no warning).")
        if linked.get("escalated"):
            concerns.append("details in the document were seen in earlier cases (under a different club name, "
                            "or in a case KEFWA confirmed)")
    elif linked and "links" in linked:
        case_id = linked.get("internal_case_id")
    if web is not None and getattr(web, "rows", None):
        counts = {s: sum(r.status == s for r in web.rows) for s in ("confirmed", "contradicted", "not_found")}
        lines.append(f"Club website check: {counts['confirmed']} confirmed, {counts['contradicted']} contradicted, "
                     f"{counts['not_found']} not found.")
        if counts["contradicted"]:
            concerns.append("the club's own website contradicts the document")
    return {"kind": "document", "line": "\n".join(lines), "concerns": concerns, "case_id": case_id}


def _message_screenshot(conv: Conversation, data: bytes, mime: str, send: Send) -> None:
    from app.whatsapp_webhook import _wording_check

    send(SCREENSHOT_ACK)
    outcome = _wording_check(data, mime)
    send(report.wording_report(outcome))
    conv.findings.append(_wording_finding(outcome))
    conv.mode = None
    send(NEXT)


def _message_text(conv: Conversation, text: str, send: Send) -> None:
    messages = [{"sender": "agent", "text": line.strip()} for line in text.splitlines() if line.strip()]
    if not messages:
        send(MESSAGE_PROMPT)
        return
    outcome = get_scam_checker().check_chat(messages)
    send(report.wording_report(outcome))
    conv.findings.append(_wording_finding(outcome))
    conv.mode = None
    send(NEXT)


def _wording_finding(outcome: dict) -> dict:
    labels = [flag["label"] for flag in outcome.get("flags") or []]
    if outcome.get("unavailable"):
        line = "*Message check*: we could not read the chat, so this check did not run."
    elif not outcome.get("messages_checked"):
        line = "*Message check*: no messages from the agent were found."
    elif labels:
        line = "*Message check*: warning patterns in the agent's messages: " + "; ".join(LABEL_SHORT.get(l, l) for l in labels) + "."
    else:
        line = "*Message check*: none of the known warning patterns were found."
    concerns = ["the agent's messages show warning patterns"] if labels else []
    return {"kind": "message", "line": line, "concerns": concerns}


# --- summary and KEFWA referral ----------------------------------------------------------


def _summary(conv: Conversation, send: Send) -> None:
    if not conv.findings:
        send(NOTHING_YET)
        return
    findings_text = "\n\n".join(f["line"] for f in conv.findings)
    send("*Your OffsAid summary*\n"
         "Everything we found in this chat. These are findings, not a verdict on the offer or the person.\n\n"
         + findings_text + "\n\n" + GUIDANCE)
    concerns = list(dict.fromkeys(c for f in conv.findings for c in f["concerns"]))
    case_id = next((f.get("case_id") for f in reversed(conv.findings) if f.get("case_id")), None)
    conv.findings = []
    conv.mode = None
    if not concerns:
        send(NO_CONCERNS)
        send(START_AGAIN)
        return
    referral = {"concerns": concerns, "findings_text": findings_text, "case_id": case_id}
    if conv.contact:
        _refer(conv, referral, conv.contact, send)
    else:
        conv.pending, conv.mode = referral, "contact"
        send(ASK_CONTACT.format(reasons=_bullets(concerns)))


def _receive_contact(conv: Conversation, text: str, send: Send) -> None:
    referral = conv.pending
    if referral is None:
        conv.mode = None
        send(WELCOME)
        return
    if _norm(text) in {"skip", "no", "none", "dontshare", "noshare"}:
        contact = None
    else:
        contact = _phone(text)
        if contact is None:
            send(BAD_CONTACT)
            return
    conv.pending, conv.mode = None, None
    _refer(conv, referral, contact, send)


def _phone(text: str) -> Optional[str]:
    try:
        num = phonenumbers.parse((text or "").strip(), "KE")
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_possible_number(num):
        return None
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def refer_to_kefwa(conn, referral: dict, contact: Optional[str]) -> None:
    """Add (or update) the KEFWA review queue row. If linking already queued this case, the player's
    number and the summary are added to that row."""
    from psycopg.types.json import Jsonb

    reasons = Jsonb(referral["concerns"])
    if referral.get("case_id"):
        conn.execute(
            """insert into review_queue (case_id, reasons, origin, player_contact, findings_text)
               values (%s, %s, 'chat_summary', %s, %s)
               on conflict (case_id) do update set
                 reasons = review_queue.reasons || excluded.reasons,
                 player_contact = excluded.player_contact,
                 findings_text = excluded.findings_text,
                 status = 'open', resolved_at = null""",
            (referral["case_id"], reasons, contact, referral["findings_text"]),
        )
    else:
        conn.execute(
            "insert into review_queue (case_id, reasons, origin, player_contact, findings_text) "
            "values (null, %s, 'chat_summary', %s, %s)",
            (reasons, contact, referral["findings_text"]),
        )


def _refer(conv: Conversation, referral: dict, contact: Optional[str], send: Send) -> None:
    try:
        with linking_connection() as conn:
            refer_to_kefwa(conn, referral, contact)
    except Exception as exc:
        log.error("KEFWA referral failed (%s)", type(exc).__name__)
        send(REFERRAL_FAILED)
        send(START_AGAIN)
        return
    contact_line = (f"As we told you at the start, KEFWA receives your phone number ({contact}) and these findings, "
                    "so they can contact you." if contact else
                    "You did not share a phone number, so KEFWA cannot contact you. You can contact KEFWA directly.")
    send(REFERRED.format(reasons=_bullets(referral["concerns"]), contact_line=contact_line))
    send(START_AGAIN)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item[0].upper()}{item[1:]}" for item in items)
