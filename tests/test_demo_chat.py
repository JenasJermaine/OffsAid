"""The browser demo of the WhatsApp chat: same pipeline as the webhook, replies kept for the page."""

from __future__ import annotations

import pytest

import app.demo_chat as demo
import app.document_extraction as extraction
from app import report
from app.config import settings
from app.conversation import NEXT, WELCOME

CHAT = "testchat0001"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(settings, "network_checks_enabled", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "demo_chat_enabled", True)
    monkeypatch.setattr(extraction, "_ocr", lambda image: "")
    monkeypatch.setattr(demo, "_chats", {})


def replies(client, chat=CHAT, after=0):
    r = client.get(f"/v1/demo/chats/{chat}/messages", params={"after": after})
    assert r.status_code == 200
    return r.json()


def test_page_and_greeting(client):
    assert "OffsAid" in client.get("/demo").text
    first = replies(client)
    assert [m["text"] for m in first["messages"]] == [WELCOME] and not first["busy"]
    assert "@agent-check" in WELCOME and "@document-check" in WELCOME and "@message-check" in WELCOME


def test_greeting_shows_the_menu_and_bad_files_are_refused(client):
    replies(client)
    client.post("/v1/demo/messages", data={"chat_id": CHAT, "text": "hello"})
    client.post("/v1/demo/messages", data={"chat_id": CHAT}, files={"file": ("a.txt", b"hi", "text/plain")})
    assert [m["text"] for m in replies(client, after=1)["messages"]] == [WELCOME, report.UNSUPPORTED]


def test_forwarded_sample_runs_the_whatsapp_pipeline(client):
    samples = client.get("/v1/demo/samples").json()
    names = [s["name"] for s in samples]
    assert "lakeview_united_offer.pdf" in names
    pdf = client.get("/v1/demo/samples/lakeview_united_offer.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    replies(client)
    client.post("/v1/demo/messages", data={"chat_id": CHAT, "text": "@document-check"},
                files={"file": ("lakeview_united_offer.pdf", pdf.content, "application/pdf")})
    out = replies(client, after=1)
    texts = [m["text"] for m in out["messages"]]
    assert texts[0] == report.ACK and "OffsAid document check" in texts[1]
    assert report.LINKING_HEADING in texts[1] and texts[-1] == NEXT and not out["busy"]


def test_bad_ids_unknown_samples_and_switch_off(client, monkeypatch):
    assert client.get("/v1/demo/chats/bad id!/messages").status_code in (400, 404)
    assert client.get("/v1/demo/samples/../../.env").status_code == 404
    monkeypatch.setattr(settings, "demo_chat_enabled", False)
    assert client.get("/demo").status_code == 404
    assert client.get("/v1/demo/samples").status_code == 404
