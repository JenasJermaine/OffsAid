"""TwilioWhatsApp.download_media against a fake Twilio over real httpx (no network).

Twilio answers the media URL with 307 to a pre-signed CDN link on mms.twiliocdn.com.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.twilio_whatsapp import TwilioWhatsApp
from app.whatsapp import MediaTooLarge

MEDIA = "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages/MM1/Media/ME1"
CDN = "https://mms.twiliocdn.com/AC123/abc?Expires=1&Signature=sig&Key-Pair-Id=K"
PDF = b"%PDF-1.4 fake letter"


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "AC123")
    monkeypatch.setattr(settings, "twilio_auth_token", "secret-token")


def client(handler) -> tuple[TwilioWhatsApp, list]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return TwilioWhatsApp(httpx.Client(transport=httpx.MockTransport(record))), seen


def twilio_then_cdn(request: httpx.Request) -> httpx.Response:
    if request.url.host == "api.twilio.com":
        return httpx.Response(307, headers={"location": CDN})
    return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})


def test_follows_the_redirect_to_the_cdn_without_sending_credentials():
    twilio, seen = client(twilio_then_cdn)
    media = twilio.download_media(MEDIA)
    assert media.data == PDF and media.mime_type == "application/pdf"
    assert [r.url.host for r in seen] == ["api.twilio.com", "mms.twiliocdn.com"]
    assert "authorization" in seen[0].headers           # the account login goes to api.twilio.com
    assert "authorization" not in seen[1].headers       # never to the CDN


def test_direct_download_still_works():
    twilio, seen = client(lambda r: httpx.Response(200, content=PDF, headers={"content-type": "image/png"}))
    assert twilio.download_media(MEDIA).mime_type == "image/png"
    assert len(seen) == 1


def test_redirect_away_from_twilio_is_refused():
    twilio, seen = client(lambda r: httpx.Response(307, headers={"location": "https://evil.example/file"}))
    with pytest.raises(ValueError):
        twilio.download_media(MEDIA)
    assert len(seen) == 1                                # the foreign host is never contacted


def test_non_twilio_or_plain_http_urls_are_refused():
    twilio, seen = client(twilio_then_cdn)
    for url in ("https://example.com/media", "http://api.twilio.com/x", "https://twilio.com.evil.example/x"):
        with pytest.raises(ValueError):
            twilio.download_media(url)
    assert seen == []


def test_redirect_loop_stops():
    twilio, seen = client(lambda r: httpx.Response(307, headers={"location": MEDIA}))
    with pytest.raises(ValueError):
        twilio.download_media(MEDIA)
    assert len(seen) == 4


def test_size_limit_applies_after_the_redirect(monkeypatch):
    monkeypatch.setattr(settings, "max_media_bytes", 10)
    twilio, _ = client(twilio_then_cdn)
    with pytest.raises(MediaTooLarge):
        twilio.download_media(MEDIA)
