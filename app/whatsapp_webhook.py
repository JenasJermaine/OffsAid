"""WhatsApp Cloud API webhook: a player forwards an offer, we reply with the evidence.

GET  /webhook  Meta's subscription check (hub.verify_token).
POST /webhook  Incoming messages. Signed by Meta (X-Hub-Signature-256), answered at once;
               the work runs in the background. Each message goes to the OffsAid chat
               (app/conversation.py, the same flow as the web demo and the Twilio webhook).
               A document runs through _send_offer below:
                 1. "checking, this can take a minute" reply
                 2. document check (Step 2) -> first report
                    images also get the wording section (other party's messages only)
                    then cross-case linking adds "Comparison with earlier cases" (app/linking_step.py)
                 3. web-checking agent (Step 3) -> second report, when the document names a club
Each message id is processed once, because Meta retries webhooks.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError

from app import report
from app.config import settings
from app.db import SessionLocal
from app.document_checks import check_document, window_missing
from app.document_extraction import SUPPORTED_TYPES, UnsafeDocument, UnsupportedDocument
from app.hashing import hash_lookup_value
from app.linking_step import run_linking
from app.scam_runtime import check_screenshot
from app.models import InboundMessage
from app.web_agent import claims_to_check
from app.web_check_jobs import create_job, run_job
from app.whatsapp import MediaTooLarge, get_whatsapp, verify_signature

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/webhook", include_in_schema=False)
def verify_subscription(request: Request) -> PlainTextResponse:
    params = request.query_params
    token = settings.whatsapp_verify_token
    if params.get("hub.mode") == "subscribe" and token and params.get("hub.verify_token") == token:
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="Verification failed.")


@router.post("/webhook", include_in_schema=False)
async def receive(request: Request, background: BackgroundTasks) -> dict:
    raw = await request.body()
    if not verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="Bad signature.")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Body is not JSON.") from exc
    for message in _messages(payload):
        if _first_time(message.get("id", "")):
            background.add_task(handle_message, message)
    # Always 200 quickly, or Meta retries and eventually disables the webhook.
    return {"status": "ok"}


def _messages(payload: dict) -> list:
    found = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            found.extend(m for m in value.get("messages", []) or [] if isinstance(m, dict) and m.get("from"))
    return found


def _first_time(wamid: str) -> bool:
    if not wamid:
        return False
    db = SessionLocal()
    try:
        db.add(InboundMessage(wamid=wamid))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False
    finally:
        db.close()


def _send(to: str, body: str) -> None:
    if not settings.whatsapp_token:
        log.info("WHATSAPP_TOKEN is not set; not sending a %d-character reply.", len(body))
        return
    try:
        get_whatsapp().send_text(to, body)
    except httpx.HTTPError:
        log.exception("Could not send a WhatsApp reply")


def _wording_check(data: bytes, mime_type: str) -> dict:
    """Wording check for one image. A failure here still leaves the document report."""
    try:
        return check_screenshot(data, mime_type)
    except Exception:
        log.exception("Wording check failed")
        return {
            "flags": [],
            "messages_checked": 0,
            "mode": "rules_only",
            "unavailable": True,
            "note": None,
        }


def _send_offer(sender: str, data: bytes, mime: str, send, message_id: str = "", wording_for_images: bool = True) -> Optional[dict]:
    """Document check, wording check for an image, cross-case linking, first report, then
    the web check. `send(to, text)`. message_id makes linking idempotent on webhook retries.
    Returns the results (for the chat summary), or None when the document could not be checked."""
    try:
        result = check_document(data, mime)
    except UnsupportedDocument:
        send(sender, report.UNSUPPORTED)
        return None
    except UnsafeDocument:
        send(sender, report.TOO_LARGE)
        return None
    except Exception:
        log.exception("Document check failed")
        send(sender, report.FAILED)
        return None

    missing_window = window_missing(result.checks)
    run_agent = settings.llm_enabled and bool(claims_to_check(result.fields, missing_window))
    wording = _wording_check(data, mime) if wording_for_images and mime.startswith("image/") else None
    linked = run_linking(result, wording, data, mime, message_id, sender.removeprefix("whatsapp:"))
    reply = report.document_report(result, web_check_running=run_agent)
    if wording is not None:
        reply = reply + "\n\n" + report.wording_report(wording)
    reply = reply + "\n\n" + report.linking_report(linked)
    send(sender, reply)
    outcome = {"document": result, "wording": wording, "linking": linked, "web": None}
    if not run_agent:
        return outcome
    number = sender.removeprefix("whatsapp:")
    db = SessionLocal()
    try:
        job_id = create_job(db, player_hash=hash_lookup_value(f"wa:{number}"))
    finally:
        db.close()
    web = run_job(job_id, result.fields, missing_window)
    send(sender, report.web_report(web))
    outcome["web"] = web
    return outcome


def handle_message(message: dict) -> None:
    from app.chat_sessions import get_conversation
    from app.conversation import handle

    sender = message["from"]
    kind = message.get("type")
    text, file = "", None
    if kind == "text":
        text = (message.get("text") or {}).get("body", "")
    elif kind in {"document", "image"}:
        media = message.get(kind) or {}
        text = media.get("caption", "")
        mime = (media.get("mime_type") or "").split(";")[0].strip()
        if mime not in SUPPORTED_TYPES or not media.get("id"):
            _send(sender, report.UNSUPPORTED)
            return
        try:
            downloaded = get_whatsapp().download_media(media["id"])
        except MediaTooLarge:
            _send(sender, report.TOO_LARGE)
            return
        except (httpx.HTTPError, KeyError, ValueError):
            log.exception("Could not download WhatsApp media")
            _send(sender, report.FAILED)
            return
        file = (downloaded.data, downloaded.mime_type or mime)
    else:
        _send(sender, report.UNSUPPORTED)  # voice notes, stickers, locations...
        return
    # Meta sends the number without "+"; it is the contact KEFWA receives on referral.
    conv = get_conversation(f"whatsapp:{sender}", contact=f"+{sender.lstrip('+')}")
    handle(conv, text, file, lambda body: _send(sender, body), message.get("id", ""))
