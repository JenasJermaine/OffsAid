"""WhatsApp Cloud API webhook: a player forwards an offer, we reply with the evidence.

GET  /webhook  Meta's subscription check (hub.verify_token).
POST /webhook  Incoming messages. Signed by Meta (X-Hub-Signature-256), answered at once;
               the work runs in the background:
                 1. "checking, this can take a minute" reply
                 2. document check (Step 2) -> first report
                    images also get the wording section (other party's messages only)
                 3. web-checking agent (Step 3) -> second report, when the document names a club
Text messages get a short explanation of what to send. Each message id is processed once,
because Meta retries webhooks.
"""

from __future__ import annotations

import json
import logging

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


def _wording_reply(data: bytes, mime_type: str) -> str:
    """Wording section for one image. A failure here still leaves the document report."""
    try:
        outcome = check_screenshot(data, mime_type)
    except Exception:
        log.exception("Wording check failed")
        outcome = {
            "flags": [],
            "messages_checked": 0,
            "mode": "rules_only",
            "unavailable": True,
            "note": None,
        }
    return report.wording_report(outcome)


def _send_offer(sender: str, data: bytes, mime: str, send) -> None:
    """Document check, wording section for an image, then the web check. `send(to, text)`."""
    try:
        result = check_document(data, mime)
    except UnsupportedDocument:
        send(sender, report.UNSUPPORTED)
        return
    except UnsafeDocument:
        send(sender, report.TOO_LARGE)
        return
    except Exception:
        log.exception("Document check failed")
        send(sender, report.FAILED)
        return

    missing_window = window_missing(result.checks)
    run_agent = settings.llm_enabled and bool(claims_to_check(result.fields, missing_window))
    reply = report.document_report(result, web_check_running=run_agent)
    if mime.startswith("image/"):
        reply = reply + "\n\n" + _wording_reply(data, mime)
    send(sender, reply)
    if not run_agent:
        return
    number = sender.removeprefix("whatsapp:")
    db = SessionLocal()
    try:
        job_id = create_job(db, player_hash=hash_lookup_value(f"wa:{number}"))
    finally:
        db.close()
    web = run_job(job_id, result.fields, missing_window)
    send(sender, report.web_report(web))


def handle_message(message: dict) -> None:
    sender = message["from"]
    kind = message.get("type")
    if kind not in {"document", "image"}:
        _send(sender, report.HELP)
        return
    media = message.get(kind) or {}
    mime = (media.get("mime_type") or "").split(";")[0].strip()
    if mime not in SUPPORTED_TYPES or not media.get("id"):
        _send(sender, report.UNSUPPORTED)
        return
    _send(sender, report.ACK)
    try:
        downloaded = get_whatsapp().download_media(media["id"])
    except MediaTooLarge:
        _send(sender, report.TOO_LARGE)
        return
    except (httpx.HTTPError, KeyError, ValueError):
        log.exception("Could not download WhatsApp media")
        _send(sender, report.FAILED)
        return
    _send_offer(sender, downloaded.data, downloaded.mime_type or mime, _send)
