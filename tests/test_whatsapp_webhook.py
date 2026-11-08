"""The WhatsApp Cloud API webhook, end to end with Meta's API and the model faked."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

import app.document_extraction as extraction
import app.llm
import app.web_check_jobs as jobs
import app.whatsapp_webhook as webhook
from app import conversation
from app.config import settings
from app.document_schemas import DocumentFields, to_form
from app.models import WebCheck, WebCheckLog
from app.web_agent import ClaimRow, LogEntry, OfficialContact, WebCheckResult
from app.whatsapp import Media

LETTERS_DIR = Path(__file__).resolve().parents[1] / "data" / "test_letters"
MANIFEST = json.loads((LETTERS_DIR / "manifest.json").read_text(encoding="utf-8"))
LETTERS = {letter["file"]: letter for letter in MANIFEST["letters"]}
SCAM = "03_scam_hjk_lookalike_domain.pdf"
PLAYER = "254700000001"


class FakeWhatsApp:
    def __init__(self, media: Media) -> None:
        self.media = media
        self.sent: list = []

    def send_text(self, to: str, body: str) -> None:
        self.sent.append((to, body))

    def download_media(self, media_id: str) -> Media:
        return self.media


@pytest.fixture
def whatsapp(monkeypatch: pytest.MonkeyPatch) -> FakeWhatsApp:
    fake = FakeWhatsApp(Media((LETTERS_DIR / SCAM).read_bytes(), "application/pdf"))
    monkeypatch.setattr(webhook, "get_whatsapp", lambda: fake)
    monkeypatch.setattr(settings, "whatsapp_token", "test-token")
    monkeypatch.setattr(settings, "whatsapp_app_secret", "")
    monkeypatch.setattr(settings, "whatsapp_verify_token", "verify-me")
    monkeypatch.setattr(settings, "network_checks_enabled", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(extraction, "_ocr", lambda image: "")
    return fake


def _payload(message: dict) -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"messages": [message]}}]}]}


def _document(wamid: str = "wamid.1", mime: str = "application/pdf") -> dict:
    return {"from": PLAYER, "id": wamid, "type": "document",
            "document": {"id": "media-1", "mime_type": mime, "filename": "offer.pdf"}}


def _post(client, payload: dict, secret: str = ""):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhook", content=body, headers=headers)


def test_subscription_check(client, whatsapp) -> None:
    ok = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    bad = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "42"})
    assert bad.status_code == 403


def test_signature_is_checked_when_a_secret_is_set(client, whatsapp, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_app_secret", "app-secret")
    assert _post(client, _payload(_document())).status_code == 403
    assert _post(client, _payload(_document()), secret="app-secret").status_code == 200


def test_document_gets_an_ack_then_the_report(client, whatsapp) -> None:
    assert _post(client, _payload(_document())).status_code == 200
    bodies = [body for to, body in whatsapp.sent if to == PLAYER]
    assert bodies[0] == webhook.report.ACK
    assert "*OffsAid document check*" in bodies[1]
    assert "EUR 450" in bodies[1]  # the payment request, found even without the model
    assert "*Wording patterns in the agent's messages*" not in bodies[1]
    assert bodies[2] == conversation.NEXT  # the chat's "anything else?" prompt
    assert len(bodies) == 3  # no API key, so no web check


def test_meta_retries_are_processed_once(client, whatsapp) -> None:
    _post(client, _payload(_document("wamid.same")))
    _post(client, _payload(_document("wamid.same")))
    assert sum(body == webhook.report.ACK for _, body in whatsapp.sent) == 1


def test_image_reply_includes_the_wording_section(client, whatsapp) -> None:
    whatsapp.media = Media(
        (LETTERS_DIR / "08_whatsapp_screenshot_paybill.png").read_bytes(), "image/png"
    )
    message = {
        "from": PLAYER,
        "id": "wamid.img",
        "type": "image",
        "image": {"id": "media-1", "mime_type": "image/png"},
    }
    assert _post(client, _payload(message)).status_code == 200
    bodies = [body for to, body in whatsapp.sent if to == PLAYER]
    assert bodies[0] == webhook.report.ACK
    assert "*OffsAid document check*" in bodies[1]
    wording = bodies[1].split("*Wording patterns in the agent's messages*", 1)[1]
    assert "could not read the chat" in wording  # no API key, so the bubbles were not transcribed
    assert "scam" not in wording.lower()
    assert "safe" not in wording.lower()
    assert len(bodies) == 3  # ACK, report, next-check prompt


def test_text_and_unsupported_files_get_guidance(client, whatsapp) -> None:
    _post(client, _payload({"from": PLAYER, "id": "wamid.t", "type": "text", "text": {"body": "hi"}}))
    _post(client, _payload(_document("wamid.w", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")))
    assert [body for _, body in whatsapp.sent] == [conversation.WELCOME, webhook.report.UNSUPPORTED]


def test_status_updates_are_ignored(client, whatsapp) -> None:
    payload = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.s", "status": "delivered"}]}}]}]}
    assert _post(client, payload).status_code == 200
    assert whatsapp.sent == []


def test_web_check_runs_in_the_background_and_sends_a_second_message(
    client, whatsapp, db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    expected = LETTERS[SCAM]["expected_fields"]
    monkeypatch.setattr(app.llm, "parse_structured", 
                        lambda output_format, **_: output_format.model_validate(to_form(DocumentFields.model_validate(expected))))

    def fake_agent(fields, *, window_missing=False, on_log=None, **_):
        on_log("wikidata_club", "HJK Helsinki -> Helsingin Jalkapalloklubi")
        return WebCheckResult(
            status="completed", club=fields.club_name.value, official_website="https://www.hjk.fi/",
            rows=[ClaimRow(claim_id="sender_domain", claim="The email domain hjk-helsinki-fc.com belongs to HJK Helsinki.",
                           status="contradicted", source_url="https://www.hjk.fi/contact", note="")],
            official_contact=OfficialContact(email="info@hjk.fi", contact_page_url="https://www.hjk.fi/contact"),
            draft_message="To: info@hjk.fi\nHello HJK Helsinki,", tool_calls=3,
            log=[LogEntry(tool="wikidata_club", detail="...")],
        )

    monkeypatch.setattr(jobs, "run_web_check", fake_agent)
    _post(client, _payload(_document("wamid.web")))
    bodies = [body for _, body in whatsapp.sent]
    assert len(bodies) == 4  # ACK, report, web report, next-check prompt
    assert bodies[3] == conversation.NEXT
    assert "checking the club's own website" in bodies[1]
    assert "Contradicted" in bodies[2] and "https://www.hjk.fi/contact" in bodies[2]
    assert "info@hjk.fi" in bodies[2]

    job = db.query(WebCheck).one()
    assert job.status == "completed" and job.player_hash and PLAYER not in job.player_hash
    assert db.query(WebCheckLog).filter_by(web_check_id=job.id).count() == 1
    response = client.get(f"/v1/web-checks/{job.id}")
    assert response.json()["result"]["rows"][0]["status"] == "contradicted"


def test_web_check_api(client, whatsapp, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "run_web_check", lambda fields, **_: WebCheckResult(status="skipped", notes=["test"]))
    started = client.post("/v1/web-check", json={"fields": LETTERS[SCAM]["expected_fields"]})
    assert started.status_code == 202
    result = client.get(f"/v1/web-checks/{started.json()['id']}").json()
    assert result["status"] == "skipped"
    assert client.get("/v1/web-checks/nope").status_code == 404
