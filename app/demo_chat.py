"""Web demo of the WhatsApp chat: the same pipeline as the WhatsApp/Twilio webhooks, shown in a browser.

GET  /demo                               the chat page (app/static/demo.html)
POST /v1/demo/messages                   form: chat_id, text, optional file (PDF, PNG, JPEG)
GET  /v1/demo/chats/{chat_id}/messages   OffsAid's replies after index `after`, and whether a check is running
GET  /v1/demo/samples[/{name}]           sample offers the presenter can "forward"

Messages go to the OffsAid chat (app/conversation.py): a menu, @agent-check, @document-check and
@message-check, a summary on "done", and KEFWA referral. Documents go through whatsapp_webhook._send_offer,
the same checks a WhatsApp document goes through. Replies are kept in memory for the page instead of sent.
DEMO_CHAT_ENABLED=false turns all of this off (404).
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app import report
from app.config import settings
from app.conversation import WELCOME, Conversation, handle
from app.document_extraction import MAX_UPLOAD_BYTES, SUPPORTED_TYPES

log = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)

ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).parent / "static" / "demo.html"
LETTERS = ROOT / "data" / "test_letters"
SAMPLES = {
    "lakeview_united_offer.pdf": ("Lakeview United offer (reuses simulated contacts)", ROOT / "data" / "demo" / "lakeview_united_offer.pdf"),
    "02_scam_selangor_gmail_mpesa.pdf": ("Selangor offer, M-Pesa fee", LETTERS / "02_scam_selangor_gmail_mpesa.pdf"),
    "01_genuine_trial_invitation_kups.pdf": ("KuPS trial invitation", LETTERS / "01_genuine_trial_invitation_kups.pdf"),
    "08_whatsapp_screenshot_paybill.png": ("Agent chat screenshot", LETTERS / "08_whatsapp_screenshot_paybill.png"),
}
MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
CHAT_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_CHATS = 200
MAX_MESSAGES = 300


@dataclass
class Chat:
    conversation: Conversation
    messages: list = field(default_factory=list)
    running: int = 0
    touched: float = field(default_factory=time.monotonic)


_chats: dict[str, Chat] = {}
_lock = threading.Lock()


def _enabled() -> None:
    if not settings.demo_chat_enabled:
        raise HTTPException(status_code=404, detail="Not found.")


def _get_chat(chat_id: str) -> Chat:
    """Find or open a chat. A new chat starts with the bot's greeting, as on WhatsApp."""
    if not CHAT_ID.match(chat_id):
        raise HTTPException(status_code=400, detail="Bad chat id.")
    with _lock:
        chat = _chats.get(chat_id)
        if chat is None:
            if len(_chats) >= MAX_CHATS:
                oldest = min(_chats, key=lambda k: _chats[k].touched)
                del _chats[oldest]
            # The web demo has no WhatsApp number: the chat asks for one if the case is referred to KEFWA.
            chat = _chats[chat_id] = Chat(Conversation(sender=f"demo-{chat_id}"))
            chat.messages.append({"text": WELCOME, "at": time.time()})
        chat.touched = time.monotonic()
        return chat


def _post(chat_id: str, body: str) -> None:
    with _lock:
        chat = _chats.get(chat_id)
        if chat is not None and len(chat.messages) < MAX_MESSAGES:
            chat.messages.append({"text": body, "at": time.time()})


def _run(chat: Chat, chat_id: str, text: str, file: Optional[tuple[bytes, str]], message_id: str) -> None:
    try:
        handle(chat.conversation, text, file, lambda body: _post(chat_id, body), message_id)
    finally:
        with _lock:
            chat = _chats.get(chat_id)
            if chat is not None:
                chat.running -= 1


@router.get("/demo")
def page() -> FileResponse:
    _enabled()
    return FileResponse(PAGE)


@router.post("/v1/demo/messages")
async def send_message(
    background: BackgroundTasks,
    chat_id: str = Form(...),
    text: str = Form(""),
    file: Optional[UploadFile] = File(None),
) -> dict:
    _enabled()
    chat = _get_chat(chat_id)
    upload = None
    if file is not None:
        mime = (file.content_type or "").split(";")[0].strip().lower()
        mime = "image/jpeg" if mime == "image/jpg" else mime
        if mime not in SUPPORTED_TYPES:
            _post(chat_id, report.UNSUPPORTED)
            return {"ok": True}
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            _post(chat_id, report.TOO_LARGE)
            return {"ok": True}
        upload = (data, mime)
    with _lock:
        chat.running += 1
    background.add_task(_run, chat, chat_id, text, upload, f"demo.{uuid.uuid4().hex}")
    return {"ok": True}


@router.get("/v1/demo/chats/{chat_id}/messages")
def messages(chat_id: str, after: int = 0) -> dict:
    _enabled()
    chat = _get_chat(chat_id)
    with _lock:
        return {"messages": chat.messages[max(after, 0):], "total": len(chat.messages), "busy": chat.running > 0}


@router.get("/v1/demo/samples")
def samples() -> list[dict]:
    _enabled()
    return [{"name": name, "label": label, "mime": MIME[path.suffix.lower()]}
            for name, (label, path) in SAMPLES.items() if path.exists()]


@router.get("/v1/demo/samples/{name}")
def sample(name: str) -> FileResponse:
    _enabled()
    if name not in SAMPLES or not SAMPLES[name][1].exists():
        raise HTTPException(status_code=404, detail="No such sample.")
    path = SAMPLES[name][1]
    return FileResponse(path, media_type=MIME[path.suffix.lower()], filename=name)
