"""The OffsAid chat: commands, the three checks run separately, the summary and the KEFWA referral."""

from __future__ import annotations

from pathlib import Path

import pytest

import app.conversation as conv_mod
import app.document_extraction as extraction
from app import report
from app.config import settings
from app.conversation import Conversation, handle, parse_agent_details, parse_command

ROOT = Path(__file__).resolve().parents[1]
LAKEVIEW = (ROOT / "data" / "demo" / "lakeview_united_offer.pdf").read_bytes()


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(settings, "network_checks_enabled", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(extraction, "_ocr", lambda image: "")


class Chat:
    def __init__(self, contact=None):
        self.conv = Conversation(sender="demo-test", contact=contact)
        self.out: list[str] = []

    def say(self, text="", file=None):
        start = len(self.out)
        handle(self.conv, text, file, self.out.append, "")
        return self.out[start:]


# ------------------------------------------------------------------ parsing


@pytest.mark.parametrize("text,command", [
    ("@agent-check", "agent"), ("@Agent Check", "agent"), ("agent check", "agent"), ("1", "agent"),
    ("@document-check", "document"), ("@doc", "document"), ("@message-check", "message"), ("3", "message"),
    ("@done", "done"), ("@ done", "done"), ("done", None), ("That's all", None), ("no", None), ("end", None),
    ("menu", "menu"), ("Hi", "menu"), ("hello there", None),
])
def test_commands(text, command):
    assert parse_command(text)[0] == command


def test_command_with_details_and_strict_mode():
    assert parse_command("@agent-check Name: James Otieno") == ("agent", "Name: James Otieno")
    assert parse_command("@message-check\nSend the fee") == ("message", "Send the fee")
    assert parse_command("Hello", strict=True) == (None, "Hello")          # a pasted chat is not the menu
    assert parse_command("@hello", strict=True)[0] == "menu"
    assert parse_command("@done", strict=True)[0] == "done"
    assert parse_command("done", strict=True)[0] is None


def test_agent_details():
    assert parse_agent_details("Name: James Otieno\nLicence number: \nPhone: 0700 000 002\nCountry: Kenya") == {
        "name": "James Otieno", "phone": "0700 000 002", "claimed_country": "Kenya"}
    assert parse_agent_details("License no: FIFA-SIM-101") == {"licence_number": "FIFA-SIM-101"}
    assert parse_agent_details("0700 000 002") == {"phone": "0700 000 002"}
    assert parse_agent_details("KEFWA-SIM-002") == {"licence_number": "KEFWA-SIM-002"}
    assert parse_agent_details("James Otieno") == {"name": "James Otieno"}


# ------------------------------------------------------------------ the checks


def test_any_greeting_shows_the_menu_with_the_privacy_notice():
    [welcome] = Chat().say("hello")
    assert "@agent-check" in welcome and "@document-check" in welcome and "@message-check" in welcome
    assert "KEFWA then receives your phone number" in welcome


def test_agent_check_prompt_template_then_result():
    chat = Chat()
    prompt, template = chat.say("@agent-check")
    assert "Licence number*: optional" in prompt and "at least one of name, licence number or phone" in prompt
    assert template.startswith("Name:")
    result, nxt = chat.say("Name: James Otieno\nPhone: 0700 000 002")
    assert "*On the licensed agents list*" in result and "never means the offer is real" in result
    assert nxt == conv_mod.NEXT


def test_agent_check_in_one_message_not_found_and_empty():
    chat = Chat()
    result, _ = chat.say("@agent-check Name: Nobody Atall")
    assert "*Not found on the licensed agents list*" in result and "does not prove the person is not an agent" in result
    chat.say("@agent-check")
    assert chat.say("Licence number: ") == [conv_mod.AGENT_NEED_ONE]


def test_document_check_explains_what_to_send_then_checks_it():
    chat = Chat()
    [prompt] = chat.say("@document-check")
    assert "trial or academy invitation" in prompt and "pre-contract" in prompt and "payment request" in prompt
    assert chat.say("what now?") == [conv_mod.DOCUMENT_WAITING]
    ack, doc_report, nxt = chat.say("", (LAKEVIEW, "application/pdf"))
    assert ack == report.ACK and "OffsAid document check" in doc_report and nxt == conv_mod.NEXT
    assert chat.conv.findings[0]["kind"] == "document" and chat.conv.findings[0]["concerns"]


def test_message_check_with_pasted_text_starting_with_hello():
    chat = Chat()
    chat.say("@message-check")
    wording, nxt = chat.say("Hello my friend\nSend the registration fee of KES 10,000 to my M-Pesa today")
    assert report.WORDING_HEADING in wording and nxt == conv_mod.NEXT
    assert "fee" in chat.conv.findings[0]["line"]


# ------------------------------------------------------------------ summary and referral


def test_done_without_checks():
    assert Chat().say("@done") == [conv_mod.NOTHING_YET]


def test_summary_without_concerns_is_not_referred_and_not_a_verdict():
    chat = Chat()
    chat.say("@agent-check Name: James Otieno")
    summary, no_concerns, again = chat.say("@done")
    assert "*Your OffsAid summary*" in summary and "*Agent check* (James Otieno): on the licensed agents list." in summary
    assert no_concerns == conv_mod.NO_CONCERNS and again == conv_mod.START_AGAIN
    assert "scammer" not in summary.lower() and chat.conv.findings == []


def test_summary_with_concerns_asks_for_a_number_then_refers(monkeypatch):
    referred = []
    monkeypatch.setattr(conv_mod, "_refer", lambda conv, referral, contact, send: referred.append((referral, contact)))
    chat = Chat()
    chat.say("@agent-check Name: Nobody Atall")
    chat.say("@document-check", (LAKEVIEW, "application/pdf"))
    summary, ask = chat.say("@done")
    assert "*Agent check*" in summary and "*Document check*" in summary and conv_mod.GUIDANCE in summary
    assert "KEFWA will receive your phone number" in ask
    assert chat.say("not a number") == [conv_mod.BAD_CONTACT]
    chat.say("0712 345 678")
    [(referral, contact)] = referred
    assert contact == "+254712345678"
    assert "the agent was not found on the licensed agents list" in referral["concerns"]
    assert "*Document check*" in referral["findings_text"]


def test_known_whatsapp_number_is_used_without_asking_and_sqlite_fails_softly():
    chat = Chat(contact="+254712345678")
    chat.say("@agent-check Name: Nobody Atall")
    summary, failed, again = chat.say("@done")
    assert failed == conv_mod.REFERRAL_FAILED                 # SQLite: the queue lives in Postgres


def test_skip_sends_without_a_number(monkeypatch):
    referred = []
    monkeypatch.setattr(conv_mod, "_refer", lambda conv, referral, contact, send: referred.append(contact))
    chat = Chat()
    chat.say("@agent-check Name: Nobody Atall")
    chat.say("@done")
    chat.say("skip")
    assert referred == [None]


# ------------------------------------------------------------------ against Postgres (OFFSAID_TEST_DB)
from test_linking_step import needs_db, pg  # noqa: E402,F401


@needs_db
def test_referral_lands_in_the_kefwa_queue_with_the_number(pg, client, monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def same():
        yield pg

    monkeypatch.setattr(conv_mod, "linking_connection", same)
    monkeypatch.setattr(settings, "admin_password", "secret")
    # Offline, the pattern fallback does not read the club name; the vision model does. Simulate that.
    import app.document_checks as checks
    from app.document_schemas import Quoted

    original = checks.extract_fields

    def with_club(doc):
        fields, method, notes = original(doc)
        fields.club_name = Quoted(value="Lakeview United FC", quote="LAKEVIEW UNITED FC")
        return fields, method, notes

    monkeypatch.setattr(checks, "extract_fields", with_club)

    chat = Chat()
    chat.say("@document-check", (LAKEVIEW, "application/pdf"))         # till 9990002 = sim-02: escalated
    assert len(pg.execute("select id from review_queue").fetchall()) == 1
    chat.say("@done")
    referred = chat.say("0712 345 678")
    assert referred[0].startswith("*Sent to KEFWA for review*") and "+254712345678" in referred[0]

    rows = pg.execute("select origin, player_contact, findings_text, case_id from review_queue").fetchall()
    assert len(rows) == 1                                              # added to the linking escalation's row
    origin, contact, findings, case_id = rows[0]
    # Linking escalated it first (origin stays "linking"); the referral added the number and the summary.
    assert origin == "linking" and contact == "+254712345678" and "*Document check*" in findings and case_id

    other = Chat(contact="+254711000000")                              # agent check only: no stored case
    other.say("@agent-check Name: Nobody Atall")
    other.say("@done")
    items = client.get("/admin/review-queue", auth=("kefwa", "secret")).json()
    assert {i["player_contact"] for i in items} == {"+254712345678", "+254711000000"}
    loose = next(i for i in items if i["case_id"] is None)
    assert client.post(f"/admin/review-queue/{loose['id']}/resolve", json={"note": "called"}, auth=("kefwa", "secret")).status_code == 200
    assert len(client.get("/admin/review-queue", auth=("kefwa", "secret")).json()) == 1
    assert client.delete(f"/admin/review-queue/{loose['id']}", auth=("kefwa", "secret")).json()["deleted"]
    assert "KEFWA review queue" in client.get("/admin/review", auth=("kefwa", "secret")).text


def test_only_at_done_gives_the_summary():
    chat = Chat()
    chat.say("@message-check")
    chat.say("Pay the clearance fee today")
    for word in ("no", "done", "end", "that's all"):
        assert not any(m.startswith("*Your OffsAid summary*") for m in chat.say(word))
    assert chat.say("@done")[0].startswith("*Your OffsAid summary*")


def test_agent_check_with_a_file_asks_for_text():
    chat = Chat()
    assert chat.say("@agent-check", file=(LAKEVIEW, "application/pdf")) == [conv_mod.AGENT_NO_FILE, conv_mod.AGENT_TEMPLATE]
    assert chat.conv.findings == []          # the file was not checked
    reply = chat.say("Name: James Otieno")   # the chat now waits for the agent's details
    assert reply[0].startswith("*Agent check result*")


# ------------------------------------------------------------------ @restart


def test_restart_commands_need_the_at():
    for text in ("@restart", "@Restart", "@start-over", "@start over", "@start again"):
        assert parse_command(text)[0] == "restart", text
    for text in ("restart", "start over", "startover"):
        assert parse_command(text)[0] is None, text
    assert parse_command("@restart", strict=True)[0] == "restart"


def test_restart_asks_once_then_clears_the_wrong_check():
    chat = Chat()
    chat.say("@agent-check")
    chat.say("Name: Nobody Unknown-Person")            # e.g. the wrong details
    assert len(chat.conv.findings) == 1
    ask = chat.say("@restart")
    assert ask == [conv_mod.RESTART_CONFIRM.format(count="1 check", them="it")]
    assert len(chat.conv.findings) == 1                # nothing cleared yet
    assert chat.say("@restart") == [conv_mod.RESTART_DONE, conv_mod.WELCOME]
    assert chat.conv.findings == [] and chat.conv.mode is None
    assert chat.say("@done") == [conv_mod.NOTHING_YET]  # the wrong check is not summarised or referred


def test_anything_else_cancels_the_restart():
    chat = Chat()
    chat.say("@message-check")
    chat.say("Pay the clearance fee today")
    chat.say("@restart")
    chat.say("menu")                                   # not a second @restart
    assert len(chat.conv.findings) == 1
    assert chat.say("@restart")[0].startswith("This clears the 1 check")   # asks again


def test_restart_with_nothing_recorded():
    chat = Chat()
    assert chat.say("@restart") == [conv_mod.RESTART_NOTHING, conv_mod.WELCOME]
    chat.say("@agent-check")                           # a check started but not finished
    assert chat.say("@start-over") == [conv_mod.RESTART_DONE, conv_mod.WELCOME]
    assert chat.conv.mode is None


def test_restart_works_while_waiting_for_a_contact_number():
    chat = Chat()
    chat.say("@message-check")
    chat.say("Pay the clearance fee today")
    chat.say("@done")                                  # demo: asks for a number for KEFWA
    assert chat.conv.mode == "contact"
    assert chat.say("@restart") == [conv_mod.RESTART_DONE, conv_mod.WELCOME]
    assert chat.conv.pending is None and chat.conv.mode is None
