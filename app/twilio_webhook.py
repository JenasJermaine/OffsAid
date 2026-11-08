"""Twilio WhatsApp webhook.

POST /twilio/webhook  Form fields from Twilio. We return empty TwiML at once, then
run the same document, wording and web checks as the Meta webhook and send the
reports through Twilio's Messages API.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import Response

from app.document_extraction import SUPPORTED_TYPES
from app.twilio_whatsapp import get_twilio, public_url, valid_signature
from app.whatsapp import MediaTooLarge
from app.whatsapp_webhook import _first_time, _send_offer, report

log = logging.getLogger(__name__)
router = APIRouter()

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _addr(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("whatsapp:"):
        return value
    return f"whatsapp:{value}"


def _send(to: str, body: str) -> None:
    try:
        get_twilio().send_text(_addr(to), body)
    except httpx.HTTPError:
        log.exception("Could not send a Twilio WhatsApp reply")


def handle_twilio_message(fields: dict[str, str]) -> None:
    sender = _addr(fields.get("From", ""))
    if not sender.removeprefix("whatsapp:"):
        return
    try:
        count = int(fields.get("NumMedia") or "0")
    except ValueError:
        count = 0
    if count < 1 or not fields.get("MediaUrl0"):
        _send(sender, report.HELP)
        return
    mime = (fields.get("MediaContentType0") or "").split(";")[0].strip().lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in SUPPORTED_TYPES:
        _send(sender, report.UNSUPPORTED)
        return
    _send(sender, report.ACK)
    try:
        downloaded = get_twilio().download_media(fields["MediaUrl0"])
    except MediaTooLarge:
        _send(sender, report.TOO_LARGE)
        return
    except (httpx.HTTPError, ValueError):
        log.exception("Could not download Twilio media")
        _send(sender, report.FAILED)
        return
    _send_offer(sender, downloaded.data, downloaded.mime_type or mime, _send, fields.get("MessageSid", ""))


@router.post("/twilio/webhook", include_in_schema=False)
async def receive(request: Request, background: BackgroundTasks) -> Response:
    form = await request.form()
    fields = {key: str(value) for key, value in form.items()}
    url = public_url(
        request.url.scheme,
        request.headers.get("host", request.url.netloc),
        request.url.path,
        request.url.query,
        request.headers.get("x-forwarded-proto", ""),
        request.headers.get("x-forwarded-host", ""),
    )
    if not valid_signature(url, fields, request.headers.get("X-Twilio-Signature")):
        raise HTTPException(status_code=403, detail="Bad signature.")
    message_sid = fields.get("MessageSid", "")
    if message_sid and _first_time(message_sid):
        background.add_task(handle_twilio_message, fields)
    elif not message_sid:
        log.info("Twilio webhook had no MessageSid; ignoring it.")
    return Response(content=EMPTY_TWIML, media_type="text/xml")
