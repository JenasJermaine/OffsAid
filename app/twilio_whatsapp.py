"""Twilio WhatsApp: validate the webhook, download media, send a text reply.

Twilio calls us with form fields, then we answer later through the Messages API.
The player's number and the message text are not written to the log.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.whatsapp import Media, MediaTooLarge, split_message

log = logging.getLogger(__name__)


def signature(url: str, params: dict[str, str], token: str) -> str:
    """Twilio's X-Twilio-Signature: HMAC-SHA1 of the public URL plus sorted form fields."""
    payload = url + "".join(key + params[key] for key in sorted(params))
    digest = hmac.new(token.encode(), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def valid_signature(url: str, params: dict[str, str], header: str | None) -> bool:
    token = settings.twilio_auth_token
    if not token:
        return True
    if not header:
        return False
    return hmac.compare_digest(signature(url, params, token), header)


def public_url(scheme: str, host: str, path: str, query: str, forwarded_proto: str, forwarded_host: str) -> str:
    """The URL Twilio signed. ngrok terminates TLS, so the local request is plain HTTP."""
    proto = (forwarded_proto or scheme).split(",")[0].strip()
    signed_host = (forwarded_host or host).split(",")[0].strip()
    tail = f"?{query}" if query else ""
    return f"{proto}://{signed_host}{path}{tail}"


class TwilioWhatsApp:
    def __init__(self, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(timeout=30.0)

    def _auth(self) -> tuple[str, str]:
        return (settings.twilio_account_sid, settings.twilio_auth_token)

    def send_text(self, to: str, body: str) -> None:
        if not settings.twilio_auth_token or not settings.twilio_whatsapp_from:
            log.info("Twilio WhatsApp is not configured; not sending a %d-character reply.", len(body))
            return
        url = (
            f"{settings.twilio_api_base}/2010-04-01/Accounts/"
            f"{settings.twilio_account_sid}/Messages.json"
        )
        for chunk in split_message(body):
            response = self._http.post(
                url,
                auth=self._auth(),
                data={"From": settings.twilio_whatsapp_from, "To": to, "Body": chunk},
            )
            if response.status_code >= 400:
                log.error("Twilio send failed %s", response.status_code)
                response.raise_for_status()

    def download_media(self, media_url: str) -> Media:
        host = urlparse(media_url).hostname or ""
        if host != "api.twilio.com" and not host.endswith(".twilio.com"):
            raise ValueError("Media URL is not on Twilio")
        response = self._http.get(media_url, auth=self._auth())
        response.raise_for_status()
        if len(response.content) > settings.max_media_bytes:
            raise MediaTooLarge(media_url)
        mime = response.headers.get("content-type", "").split(";")[0].strip()
        return Media(data=response.content, mime_type=mime)


_client: TwilioWhatsApp | None = None


def get_twilio() -> TwilioWhatsApp:
    global _client
    if _client is None:
        _client = TwilioWhatsApp()
    return _client
