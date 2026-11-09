"""Twilio WhatsApp: validate the webhook, download media, send a text reply.

Twilio calls us with form fields, then we answer later through the Messages API.
The player's number and the message text are not written to the log.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import threading
import time
from urllib.parse import urljoin, urlparse

import httpx

from app.config import settings
from app.whatsapp import Media, MediaTooLarge, split_message

log = logging.getLogger(__name__)

# Twilio rejects a message body over 1,600 characters (error 21617), lower than Meta's 4,096,
# so replies are split smaller here. A little under the limit leaves room for how Twilio counts.
TWILIO_TEXT_LIMIT = 1500
# Twilio does not guarantee that messages sent in quick succession arrive in order, so messages
# to the same number are spaced out: the parts of a long report, the report and the next prompt.
SEND_GAP_SECONDS = 1.0


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
        self._last_sent: dict[str, float] = {}
        self._gap_lock = threading.Lock()

    def _auth(self) -> tuple[str, str]:
        return (settings.twilio_account_sid, settings.twilio_auth_token)

    def _wait_turn(self, to: str) -> None:
        """Keep SEND_GAP_SECONDS between messages to one number (runs in a background task)."""
        with self._gap_lock:
            wait = self._last_sent.get(to, 0.0) + SEND_GAP_SECONDS - time.monotonic()
            self._last_sent[to] = time.monotonic() + max(wait, 0.0)
        if wait > 0:
            time.sleep(wait)

    def send_text(self, to: str, body: str) -> None:
        if not settings.twilio_auth_token or not settings.twilio_whatsapp_from:
            log.info("Twilio WhatsApp is not configured; not sending a %d-character reply.", len(body))
            return
        url = (
            f"{settings.twilio_api_base}/2010-04-01/Accounts/"
            f"{settings.twilio_account_sid}/Messages.json"
        )
        for chunk in split_message(body, limit=TWILIO_TEXT_LIMIT):
            self._wait_turn(to)
            response = self._http.post(
                url,
                auth=self._auth(),
                data={"From": settings.twilio_whatsapp_from, "To": to, "Body": chunk},
            )
            if response.status_code >= 400:
                try:
                    code = response.json().get("code")
                except ValueError:
                    code = None
                # Twilio's error code only (e.g. 21212 bad From, 63007 sender not on WhatsApp,
                # 21608/63015 sandbox not joined); its message text can contain phone numbers.
                log.error("Twilio send failed %s (Twilio error %s, https://www.twilio.com/docs/errors/%s)",
                          response.status_code, code, code)
                response.raise_for_status()

    def download_media(self, media_url: str) -> Media:
        """Fetch an attachment. Twilio answers the media URL with a redirect (307) to its CDN
        (mms.twiliocdn.com, a pre-signed link), so redirects are followed, but only to Twilio's own
        hosts, and the account credentials are sent to api.twilio.com only, never to the CDN."""
        if not _is_twilio_media(media_url):
            raise ValueError("Media URL is not on Twilio")
        url, auth = media_url, self._auth()
        for _ in range(MAX_MEDIA_REDIRECTS + 1):
            with self._http.stream("GET", url, auth=auth, follow_redirects=False) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Twilio media redirect without a location")
                    next_url = urljoin(url, location)
                    if not _is_twilio_media(next_url):
                        raise ValueError("Twilio media redirect left Twilio's hosts")
                    if urlparse(next_url).hostname != urlparse(url).hostname:
                        auth = None   # the CDN link is pre-signed; never forward the account credentials
                    url = next_url
                    continue
                response.raise_for_status()
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > settings.max_media_bytes:
                    raise MediaTooLarge(media_url)
                body = bytearray()
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > settings.max_media_bytes:
                        raise MediaTooLarge(media_url)
                mime = response.headers.get("content-type", "").split(";")[0].strip()
                return Media(data=bytes(body), mime_type=mime)
        raise ValueError("Too many redirects while downloading Twilio media")


MAX_MEDIA_REDIRECTS = 3
TWILIO_MEDIA_DOMAINS = ("twilio.com", "twiliocdn.com")


def _is_twilio_media(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == d or host.endswith("." + d) for d in TWILIO_MEDIA_DOMAINS)


_client: TwilioWhatsApp | None = None


def get_twilio() -> TwilioWhatsApp:
    global _client
    if _client is None:
        _client = TwilioWhatsApp()
    return _client
