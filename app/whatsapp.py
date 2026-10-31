"""Meta WhatsApp Cloud API: send text replies and download media players forward."""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass

import httpx

from app.config import settings

log = logging.getLogger(__name__)

WHATSAPP_TEXT_LIMIT = 4096


class MediaTooLarge(Exception):
    pass


@dataclass
class Media:
    data: bytes
    mime_type: str


def verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Check Meta's X-Hub-Signature-256 header (HMAC-SHA256 of the raw body)."""
    if not settings.whatsapp_app_secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.whatsapp_app_secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


def split_message(body: str, limit: int = WHATSAPP_TEXT_LIMIT) -> list[str]:
    """Split on paragraph, then line boundaries so no chunk exceeds WhatsApp's limit."""
    chunks: list[str] = []
    current = ""
    for paragraph in body.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = ""
        for line in paragraph.split("\n"):
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= limit:
                current = candidate
            else:
                chunks.append(current)
                current = line
    if current:
        chunks.append(current)
    return chunks


class WhatsAppClient:
    def __init__(self, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(timeout=30.0)

    @property
    def _base(self) -> str:
        return f"{settings.whatsapp_graph_url}/{settings.whatsapp_api_version}"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {settings.whatsapp_token}"}

    def send_text(self, to: str, body: str) -> None:
        for chunk in split_message(body):
            response = self._http.post(
                f"{self._base}/{settings.whatsapp_phone_number_id}/messages",
                headers=self._headers,
                json={
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": to,
                    "type": "text",
                    "text": {"preview_url": False, "body": chunk},
                },
            )
            if response.status_code >= 400:
                log.error("WhatsApp send failed %s: %s", response.status_code, response.text)
                response.raise_for_status()

    def download_media(self, media_id: str) -> Media:
        meta = self._http.get(f"{self._base}/{media_id}", headers=self._headers)
        meta.raise_for_status()
        info = meta.json()
        if int(info.get("file_size") or 0) > settings.max_media_bytes:
            raise MediaTooLarge(media_id)
        blob = self._http.get(info["url"], headers=self._headers)
        blob.raise_for_status()
        if len(blob.content) > settings.max_media_bytes:
            raise MediaTooLarge(media_id)
        return Media(data=blob.content, mime_type=info.get("mime_type", "").split(";")[0])


_client: WhatsAppClient | None = None


def get_whatsapp() -> WhatsAppClient:
    global _client
    if _client is None:
        _client = WhatsAppClient()
    return _client
