"""Tests that need NO model (rules, message selection, plumbing).

Run: pytest tests/test_scam_language.py
The real-model smoke check is scripts/smoke_scam_language.py.
"""
from app.scam_language import (ScamLanguageChecker, fee_rule, negated_guarantee, negated_visa_claim,
                               select_agent_messages, LABELS)


class ZeroModel(ScamLanguageChecker):
    """Stand-in: every score is a fixed value, so only the rules and plumbing are tested."""
    def __init__(self, value=0.0, hot=None):
        super().__init__(tokenizer=object(), model=object())   # pretend a model exists
        self.value, self.hot = value, hot or {}

    def _score(self, units):
        return [[self.hot.get(l, self.value) for l in self.labels] for _ in units]


def test_negation_cancels_guarantee():
    assert negated_guarantee("We can't guarantee anything, but come for the trial")
    assert negated_guarantee("Hakuna mtu atakuhakikishia visa")
    assert negated_guarantee("Be careful of anyone who guarantees you a contract")
    assert not negated_guarantee("Your contract is 100% guaranteed")
    assert not negated_guarantee("Your trial is on Saturday")           # no guarantee word at all


def test_visa_negation():
    assert negated_visa_claim("We cannot promise any visa, the embassy decides")
    assert not negated_visa_claim("Your visa is approved")
    assert not negated_visa_claim("We can't guarantee the visa, but your flight is booked")


def test_fee_rule_fires():
    for t in ["Only thing left is paying the courier for your papers.",
              "DHL is holding your contract until the delivery fee is paid.",
              "The doctor must be paid before your medical, send the money to my sister's number.",
              "Tuma ile pesa ya customs ndio documents zitoke",
              "Just settle the clearance charges."]:
        assert fee_rule(t), t


def test_fee_rule_stays_quiet():
    for t in ["Your trial is on Saturday at 9am. Bring boots and ID. There is no fee.",
              "The club will pay for your courier and documents, you pay nothing.",
              "Please send your medical results and passport copy by email.",
              "Hakuna malipo yoyote kwa majaribio."]:
        assert not fee_rule(t), t


def test_select_agent_messages_drops_player_and_quotes():
    msgs = [{"sender": "player", "text": "ok how much"},
            {"sender": "agent", "text": "Send 10k today"},
            {"sender": "agent", "text": "ok how much", "quoted": True},
            {"sender": "agent", "text": "   "},
            {"sender": "unknown", "text": "Join my Telegram"}]
    kept = select_agent_messages(msgs)
    assert [m["text"] for m in kept] == ["Send 10k today", "Join my Telegram"]


def test_rule_adds_fee_when_model_is_silent():
    c = ZeroModel(0.0)
    out = c.check_chat({"messages": [{"sender": "agent", "text": "Congratulations. Just cover the courier charges."}]})
    flags = {f["label"]: f for f in out["flags"]}
    assert flags["advance_fee_request"]["source"] == "rule"
    assert out["messages_checked"] == 1


def test_negation_suppresses_model_flag():
    c = ZeroModel(0.0, hot={"guaranteed_outcome": 0.99})
    out = c.classify([{"sender": "agent", "text": "We can't guarantee anything, but come for the trial on Saturday, it is free."}])
    assert [f["label"] for f in out["flags"]] == []


def test_player_messages_never_flagged():
    c = ZeroModel(0.99)
    out = c.check_chat([{"sender": "player", "text": "Send 10k today, don't tell anyone"}])
    assert out["flags"] == [] and out["messages_checked"] == 0


def test_rules_only_mode_without_model():
    c = ScamLanguageChecker()                       # no model
    out = c.check_chat([{"sender": "agent", "text": "Pay the clearance fee today."}])
    assert out["mode"] == "rules_only"
    assert out["flags"][0]["label"] == "advance_fee_request"
    assert set(c.labels) == set(LABELS)


def test_adapter_drops_player_quotes_and_non_text():
    from app.chat_extraction import ChatMessage, ChatShot, to_checker_input

    shot = ChatShot(
        app="whatsapp",
        group_chat=False,
        messages=[
            ChatMessage(sender="player", text="ok how much", type="text", quoted_reply="", time="10:00"),
            ChatMessage(
                sender="agent",
                text="Send the courier fee today",
                type="text",
                quoted_reply="ok how much",
                time="10:01",
            ),
            ChatMessage(sender="agent", text="listened", type="voice", quoted_reply="", time="10:02"),
            ChatMessage(sender="agent", text="scan", type="image", quoted_reply="", time="10:03"),
            ChatMessage(sender="agent", text="Messages are end-to-end encrypted", type="system", quoted_reply="", time=""),
            ChatMessage(sender="unknown", text="Join my Telegram", type="text", quoted_reply="", time=""),
        ],
    )
    kept = select_agent_messages(to_checker_input(shot)["messages"])
    assert [m["text"] for m in kept] == ["Send the courier fee today", "Join my Telegram"]


def test_screenshot_fixture_checks_only_the_left_side():
    """The simulated WhatsApp fixture: white bubbles are the agent, the green one is the player."""
    messages = [
        {"sender": "agent", "text": "Congratulations Brian! You have been selected for the Selangor FC trials in Malaysia."},
        {"sender": "agent", "text": "Trials start 15 December. Visa and flight are already sorted on our side."},
        {"sender": "agent", "text": "You only need to pay KES 15,000 registration through Paybill 247247 Account 0701552903 by tomorrow 5pm."},
        {"sender": "agent", "text": "After that the slot goes to the next player. Don't tell your current coach until you have travelled."},
        {"sender": "player", "text": "Okay coach, can I pay half now?"},
        {"sender": "agent", "text": "No. Full amount, other boys are waiting."},
    ]
    out = ScamLanguageChecker().check_chat(messages)
    assert out["messages_checked"] == 5
    assert all("pay half" not in flag["quote"] for flag in out["flags"])


def test_wording_report_quotes_patterns_and_does_not_judge():
    from app.report import WORDING_NO_AGENT_TEXT, WORDING_NONE_FOUND, wording_report

    empty = wording_report({"flags": [], "messages_checked": 2, "mode": "model+rules", "unavailable": False})
    assert WORDING_NONE_FOUND in empty
    assert "safe" not in empty.lower()
    assert "scam" not in empty.lower()

    missing = wording_report({"flags": [], "messages_checked": 0, "mode": "model+rules", "unavailable": False})
    assert WORDING_NO_AGENT_TEXT in missing
    assert "no problems" not in missing.lower()

    flagged = wording_report({
        "flags": [{
            "label": "advance_fee_request",
            "quote": "Pay the clearance fee today.",
            "score": 0.5,
            "source": "rule",
            "meaning": "Asks for a payment or fee before any signed club contract.",
        }],
        "messages_checked": 1,
        "mode": "rules_only",
        "unavailable": False,
    })
    assert "Pay the clearance fee today." in flagged
    assert "Asks for a payment or fee" in flagged
    assert "0.5" not in flagged
    assert "%" not in flagged
    assert "rules_only" not in flagged
    assert "fee-wording rule" in flagged


def _png() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_messages_route_skips_the_player_and_the_legitimate_line(client):
    response = client.post("/v1/scam-language/messages", json={"messages": [
        {"sender": "agent", "text": "Only thing left is paying the courier for your papers."},
        {"sender": "agent", "text": "Your trial is on Saturday at 9am. Bring boots and ID. There is no fee."},
        {"sender": "player", "text": "Send 10k today, don't tell anyone"},
    ]})
    assert response.status_code == 200
    body = response.json()
    assert body["messages_checked"] == 2
    assert body["mode"] == "rules_only"
    assert {flag["label"] for flag in body["flags"]} == {"advance_fee_request"}
    assert body["flags"][0]["source"] == "rule"
    assert "Saturday" not in body["flags"][0]["quote"]
    assert "don't tell" not in body["flags"][0]["quote"]


def test_screenshot_route_reads_only_the_other_party(client, monkeypatch):
    from app.chat_extraction import ChatMessage, ChatRead, ChatShot

    shot = ChatShot(
        app="whatsapp",
        group_chat=True,
        messages=[
            ChatMessage(
                sender="agent",
                text="Only thing left is paying the courier for your papers.",
                type="text",
                quoted_reply="can I pay half now?",
                time="10:01",
            ),
            ChatMessage(sender="player", text="can I pay half now?", type="text", quoted_reply="", time="10:00"),
            ChatMessage(
                sender="agent",
                text="Your trial is on Saturday at 9am. Bring boots and ID. There is no fee.",
                type="text",
                quoted_reply="",
                time="10:02",
            ),
            ChatMessage(sender="agent", text="", type="voice", quoted_reply="", time="10:03"),
        ],
    )
    monkeypatch.setattr("app.scam_runtime.extract_chat", lambda data, mime: ChatRead(shot, False))
    response = client.post(
        "/v1/scam-language/screenshot",
        files={"screenshot": ("chat.png", _png(), "image/png")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["messages_checked"] == 2
    assert body["app"] == "whatsapp"
    assert [m["text"] for m in body["agent_messages"]] == [
        "Only thing left is paying the courier for your papers.",
        "Your trial is on Saturday at 9am. Bring boots and ID. There is no fee.",
    ]
    assert "less reliable" in (body["note"] or "")
    assert "can I pay half" not in body["report_text"]
    assert "courier" in body["report_text"]
    assert "0.5" not in body["report_text"]
    assert "scam" not in body["report_text"].lower()


def test_screenshot_without_a_vision_model_does_not_invent_messages(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "")
    response = client.post(
        "/v1/scam-language/screenshot",
        files={"screenshot": ("chat.png", _png(), "image/png")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["unavailable"] is True
    assert body["messages_checked"] == 0
    assert body["agent_messages"] == []
    assert "could not read the chat" in body["report_text"]
    assert "no problems" not in body["report_text"].lower()


def test_screenshot_route_rejects_non_images_and_oversized_files(client, monkeypatch):
    from app import main as main_module

    rejected = client.post(
        "/v1/scam-language/screenshot",
        files={"screenshot": ("letter.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert rejected.status_code == 415
    monkeypatch.setattr(main_module, "MAX_UPLOAD_BYTES", 8)
    oversized = client.post(
        "/v1/scam-language/screenshot",
        files={"screenshot": ("chat.png", _png(), "image/png")},
    )
    assert oversized.status_code == 413
