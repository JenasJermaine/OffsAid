"""Twilio's WhatsApp webhook, with the Messages API faked."""

from __future__ import annotations

from pathlib import Path

import pytest

import app.document_extraction as extraction
import app.twilio_webhook as twilio
from app import conversation
from app.config import settings
from app.twilio_whatsapp import signature
from app.whatsapp import Media

LETTERS_DIR = Path(__file__).resolve().parents[1] / "data" / "test_letters"
TOKEN = "test-token"
PLAYER = "whatsapp:+254700000001"


class FakeTwilio:
    def __init__(self, media: Media) -> None:
        self.media = media
        self.sent: list = []

    def send_text(self, to: str, body: str) -> None:
        self.sent.append((to, body))

    def download_media(self, url: str) -> Media:
        return self.media


@pytest.fixture
def twilio_client(monkeypatch: pytest.MonkeyPatch) -> FakeTwilio:
    fake = FakeTwilio(Media((LETTERS_DIR / "03_scam_hjk_lookalike_domain.pdf").read_bytes(), "application/pdf"))
    monkeypatch.setattr(twilio, "get_twilio", lambda: fake)
    monkeypatch.setattr(settings, "twilio_auth_token", TOKEN)
    monkeypatch.setattr(settings, "twilio_account_sid", "AC123")
    monkeypatch.setattr(settings, "twilio_whatsapp_from", "whatsapp:+14155238886")
    monkeypatch.setattr(settings, "network_checks_enabled", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(extraction, "_ocr", lambda image: "")
    return fake


def _post(client, fields: dict, *, token: str = TOKEN, proto: str = "", host: str = ""):
    url = f"{proto or 'http'}://{host or 'testserver'}/twilio/webhook"
    headers = {"X-Twilio-Signature": signature(url, fields, token)}
    if proto:
        headers["X-Forwarded-Proto"] = proto
    if host:
        headers["X-Forwarded-Host"] = host
    return client.post("/twilio/webhook", data=fields, headers=headers)


def test_text_gets_the_menu_and_empty_twiml(client, twilio_client) -> None:
    response = _post(client, {"MessageSid": "SM1", "From": PLAYER, "Body": "hi", "NumMedia": "0"})
    assert response.status_code == 200
    assert response.text == twilio.EMPTY_TWIML
    assert response.headers["content-type"].startswith("text/xml")
    assert twilio_client.sent == [(PLAYER, conversation.WELCOME)]


def test_bad_signature_is_rejected(client, twilio_client) -> None:
    fields = {"MessageSid": "SM2", "From": PLAYER, "Body": "hi", "NumMedia": "0"}
    response = client.post(
        "/twilio/webhook",
        data=fields,
        headers={"X-Twilio-Signature": signature("https://evil.example/twilio/webhook", fields, TOKEN)},
    )
    assert response.status_code == 403
    assert twilio_client.sent == []


def test_ngrok_https_url_is_what_gets_signed(client, twilio_client) -> None:
    fields = {"MessageSid": "SM3", "From": PLAYER, "Body": "hi", "NumMedia": "0"}
    response = _post(client, fields, proto="https", host="demo.ngrok-free.app")
    assert response.status_code == 200
    assert twilio_client.sent == [(PLAYER, conversation.WELCOME)]


def test_retry_is_processed_once(client, twilio_client) -> None:
    fields = {"MessageSid": "SM-same", "From": PLAYER, "Body": "hi", "NumMedia": "0"}
    _post(client, fields)
    _post(client, fields)
    assert twilio_client.sent == [(PLAYER, conversation.WELCOME)]


def test_image_gets_the_document_report_and_wording_section(client, twilio_client) -> None:
    twilio_client.media = Media(
        (LETTERS_DIR / "08_whatsapp_screenshot_paybill.png").read_bytes(), "image/png"
    )
    fields = {
        "MessageSid": "SM-img",
        "From": PLAYER,
        "Body": "",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/2010-04-01/Accounts/AC123/Media/ME123",
        "MediaContentType0": "image/png",
    }
    assert _post(client, fields).status_code == 200
    bodies = [body for _, body in twilio_client.sent]
    assert bodies[0] == twilio.report.ACK
    assert "*OffsAid document check*" in bodies[1]
    assert "*Wording patterns in the agent's messages*" in bodies[1]


def _text(client, sid: str, body: str):
    return _post(client, {"MessageSid": sid, "From": PLAYER, "Body": body, "NumMedia": "0"})


def test_guided_chat_matches_the_demo(client, twilio_client) -> None:
    """Menu, @agent-check with its form, the result, then @done -> summary, as in /demo."""
    _text(client, "SM-a", "hello")
    _text(client, "SM-b", "@agent-check")
    _text(client, "SM-c", "Name: Nobody Unknown-Person\nCountry: Kenya")
    _text(client, "SM-d", "@done")
    bodies = [body for to, body in twilio_client.sent]
    assert all(to == PLAYER for to, _ in twilio_client.sent)
    assert bodies[0] == conversation.WELCOME
    assert bodies[1] == conversation.AGENT_PROMPT and bodies[2] == conversation.AGENT_TEMPLATE
    assert bodies[3].startswith("*Agent check result*") and "Not found" in bodies[3]
    assert bodies[4] == conversation.NEXT
    assert bodies[5].startswith("*Your OffsAid summary*")
    # The WhatsApp number is already known, so the player is not asked for one. Referral needs Postgres,
    # so on the test database it reports that the case could not be sent.
    assert conversation.ASK_CONTACT.split("{")[0] not in "".join(bodies)
    assert conversation.REFERRAL_FAILED in bodies


def test_document_check_mode_then_file(client, twilio_client) -> None:
    _text(client, "SM-e", "2")
    assert twilio_client.sent[-1][1] == conversation.DOCUMENT_PROMPT
    fields = {
        "MessageSid": "SM-f", "From": PLAYER, "Body": "", "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/2010-04-01/Accounts/AC123/Media/ME1",
        "MediaContentType0": "application/pdf",
    }
    _post(client, fields)
    bodies = [body for _, body in twilio_client.sent]
    assert bodies[1] == twilio.report.ACK
    assert "*OffsAid document check*" in bodies[2]
    assert bodies[-1] == conversation.NEXT


def test_each_number_has_its_own_chat(client, twilio_client) -> None:
    other = "whatsapp:+254700000002"
    _text(client, "SM-g", "@agent-check")
    _post(client, {"MessageSid": "SM-h", "From": other, "Body": "James Otieno", "NumMedia": "0"})
    assert twilio_client.sent[-1] == (other, conversation.WELCOME)  # not treated as agent details


def test_unsupported_file_type(client, twilio_client) -> None:
    _post(client, {"MessageSid": "SM-i", "From": PLAYER, "Body": "", "NumMedia": "1",
                   "MediaUrl0": "https://api.twilio.com/x", "MediaContentType0": "audio/ogg"})
    assert twilio_client.sent == [(PLAYER, twilio.report.UNSUPPORTED)]
