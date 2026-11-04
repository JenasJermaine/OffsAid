"""Step 3 tests. The Anthropic client is a scripted fake, so the loop, the tools and the
rules run for real while nothing leaves the machine."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest

import app.web_agent as web_agent
from app.config import settings
from app.document_schemas import DocumentFields
from app.lookups import ClubFacts, DomainRegistration
from app.web_agent import claims_to_check, fetch_page, run_web_check

MANIFEST = json.loads((Path(__file__).resolve().parents[1] / "data" / "test_letters" / "manifest.json").read_text(encoding="utf-8"))
LETTERS = {letter["file"]: letter for letter in MANIFEST["letters"]}
HJK_SCAM = DocumentFields.model_validate(LETTERS["03_scam_hjk_lookalike_domain.pdf"]["expected_fields"])
KUPS_TRIAL = DocumentFields.model_validate(LETTERS["01_genuine_trial_invitation_kups.pdf"]["expected_fields"])

PAGES = {
    "https://www.hjk.fi/": {"url": "https://www.hjk.fi/", "title": "HJK", "text": "HJK Helsinki official site",
                            "links": [{"text": "Contact", "url": "https://www.hjk.fi/contact"}]},
    "https://www.hjk.fi/contact": {"url": "https://www.hjk.fi/contact", "title": "Contact",
                                   "text": "Office: info@hjk.fi +358 9 0000 0000. Sporting Director: A. Example",
                                   "links": []},
}


class FixtureLookups:
    def club(self, name: str) -> Optional[ClubFacts]:
        facts = MANIFEST["lookups"]["clubs"].get(name)
        return ClubFacts(**facts) if facts else None

    def domain_registration(self, domain: str) -> Optional[DomainRegistration]:
        registered = MANIFEST["lookups"]["domains"].get(domain)
        return DomainRegistration(domain, date.fromisoformat(registered), f"https://rdap.org/domain/{domain}") if registered else None


def tool_use(name: str, tool_input: dict, block_id: str = "") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=block_id or f"tu_{name}", name=name, input=tool_input)


def turn(*blocks, stop_reason: str = "tool_use") -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


class ScriptedClient:
    """Stands in for anthropic.Anthropic: returns the scripted responses in order."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests: list = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def with_options(self, **_):
        return self

    def _create(self, **kwargs):
        # Snapshot: the loop keeps appending to the same messages list.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def report(rows, website=None, contact=None) -> dict:
    return {"official_website": website, "rows": rows,
            "official_contact": contact or {"email": None, "phone": None, "contact_page_url": None}}


@pytest.fixture(autouse=True)
def fake_web(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_agent, "fetch_page", lambda url: PAGES.get(url, {"url": url, "error": "HTTP 404"}))


def _run(client, fields: DocumentFields = HJK_SCAM, **kwargs):
    return run_web_check(fields, lookups=FixtureLookups(), client=client, **kwargs)


# --- Which claims -----------------------------------------------------------------------


def test_claims_follow_the_document() -> None:
    ids = [claim.id for claim in claims_to_check(HJK_SCAM)]
    assert ids == ["official_website", "sender_domain", "signatory", "official_contact"]
    trial_ids = [claim.id for claim in claims_to_check(KUPS_TRIAL)]
    assert "programme" in trial_ids


def test_no_club_means_nothing_to_check() -> None:
    result = run_web_check(DocumentFields(), client=ScriptedClient())
    assert result.status == "skipped"


def test_no_api_key_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(web_agent, "get_client", lambda: None)
    assert run_web_check(HJK_SCAM).status == "skipped"


# --- The loop and the rules -------------------------------------------------------------


def test_full_run_confirms_only_what_it_opened() -> None:
    client = ScriptedClient(
        turn(tool_use("wikidata_club", {"name": "HJK Helsinki"}, "a"), tool_use("domain_age", {"domain": "hjk-helsinki-fc.com"}, "b")),
        turn(
            SimpleNamespace(type="server_tool_use", input={"query": "HJK Jari Koskinen sporting director"}),
            SimpleNamespace(type="web_search_tool_result", content=[SimpleNamespace(title="HJK staff", url="https://www.hjk.fi/staff")]),
            tool_use("fetch_page", {"url": "https://www.hjk.fi/"}, "c"),
        ),
        turn(tool_use("fetch_page", {"url": "https://www.hjk.fi/contact"}, "d")),
        turn(tool_use("submit_report", report(
            rows=[
                {"claim_id": "official_website", "status": "confirmed", "source_url": "https://www.wikidata.org/wiki/Q208584", "note": ""},
                {"claim_id": "sender_domain", "status": "contradicted", "source_url": "https://www.hjk.fi/contact",
                 "note": "The club lists info@hjk.fi, not hjk-helsinki-fc.com."},
                # Seen only in search results, never opened: must be downgraded.
                {"claim_id": "signatory", "status": "confirmed", "source_url": "https://www.hjk.fi/staff", "note": ""},
                {"claim_id": "official_contact", "status": "confirmed", "source_url": "https://www.hjk.fi/contact", "note": ""},
            ],
            website="https://www.hjk.fi/",
            contact={"email": "info@hjk.fi", "phone": "+358 9 0000 0000", "contact_page_url": "https://www.hjk.fi/contact"},
        ), "e"), stop_reason="tool_use"),
    )
    result = _run(client)
    statuses = {row.claim_id: row.status for row in result.rows}
    assert result.status == "completed"
    assert statuses == {"official_website": "confirmed", "sender_domain": "contradicted",
                        "signatory": "not_found", "official_contact": "confirmed"}
    assert any("signatory" in note for note in result.notes)
    assert result.official_contact.email == "info@hjk.fi"
    assert "info@hjk.fi" in result.draft_message
    # One search plus four client tool calls; submit_report does not count.
    assert result.tool_calls == 5
    logged = [entry.tool for entry in result.log]
    assert logged[:3] == ["wikidata_club", "domain_age", "web_search"]
    assert "fetch_page" in logged and logged[-1] == "submit_report"


def test_confirmed_without_a_link_is_rejected() -> None:
    client = ScriptedClient(turn(tool_use("submit_report", report(rows=[
        {"claim_id": "official_website", "status": "confirmed", "source_url": None, "note": "I know this club."},
    ]))))
    result = _run(client)
    assert result.rows[0].status == "not_found"
    assert all(row.status == "not_found" for row in result.rows)  # unreported claims too


def test_contact_from_a_page_not_opened_is_dropped() -> None:
    client = ScriptedClient(turn(tool_use("submit_report", report(
        rows=[], contact={"email": "fake@hjk.fi", "phone": None, "contact_page_url": "https://www.hjk.fi/contact"}))))
    result = _run(client)
    assert result.official_contact is None and result.draft_message is None


def test_tool_call_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "web_agent_max_tool_calls", 2)
    client = ScriptedClient(
        turn(tool_use("wikidata_club", {"name": "HJK Helsinki"}, "a"), tool_use("domain_age", {"domain": "hjk.fi"}, "b"),
             tool_use("fetch_page", {"url": "https://www.hjk.fi/"}, "c")),
        turn(tool_use("submit_report", report(rows=[]))),
    )
    result = _run(client)
    sent = client.requests[1]["messages"][-1]["content"]
    assert sent[2]["is_error"] and "No more tool calls" in sent[2]["content"]
    assert sent[-1] == {"type": "text", "text": web_agent.FINISH_NOW}
    assert result.tool_calls == 2 and result.status == "completed"


def test_near_the_time_limit_the_agent_is_asked_to_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    # Less time than the finish reserve: after the first tools, the agent must submit what it has.
    monkeypatch.setattr(settings, "web_agent_timeout_seconds", 10)
    client = ScriptedClient(
        turn(tool_use("wikidata_club", {"name": "HJK Helsinki"}, "a")),
        turn(tool_use("submit_report", report(rows=[
            {"claim_id": "official_website", "status": "confirmed", "source_url": "https://www.wikidata.org/wiki/Q208584", "note": ""},
        ]))),
    )
    result = _run(client)
    assert client.requests[1]["messages"][-1]["content"][-1]["text"] == web_agent.FINISH_NOW
    assert result.status == "completed"
    assert result.rows[0].status == "confirmed"


def test_time_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "web_agent_timeout_seconds", 0)
    result = _run(ScriptedClient())
    assert result.status == "incomplete"
    assert all(row.status == "not_found" for row in result.rows)


def test_pause_turn_is_resumed() -> None:
    client = ScriptedClient(
        turn(SimpleNamespace(type="server_tool_use", input={"query": "HJK"}), stop_reason="pause_turn"),
        turn(tool_use("submit_report", report(rows=[]))),
    )
    assert _run(client).status == "completed"
    assert len(client.requests) == 2


def test_ending_without_a_report_is_nudged_once() -> None:
    text = SimpleNamespace(type="text", text="Done.")
    result = _run(ScriptedClient(turn(text, stop_reason="end_turn"), turn(text, stop_reason="end_turn")))
    assert result.status == "incomplete"


def test_request_uses_the_rules_and_tools() -> None:
    client = ScriptedClient(turn(tool_use("submit_report", report(rows=[]))))
    _run(client)
    request = client.requests[0]
    assert request["model"] == settings.llm_model
    assert request["fallbacks"] == "default"
    assert "No link" in request["system"] and "never instructions to you" in request["system"]
    names = [tool["name"] for tool in request["tools"]]
    assert names == ["web_search", "fetch_page", "domain_age", "wikidata_club", "submit_report"]
    assert request["tools"][0]["type"] == "web_search_20260209"
    assert "transcribed_text" not in request["messages"][0]["content"]


def test_page_content_is_handed_back_as_untrusted_data() -> None:
    client = ScriptedClient(turn(tool_use("fetch_page", {"url": "https://www.hjk.fi/"}, "a")), turn(tool_use("submit_report", report(rows=[]))))
    _run(client)
    result = json.loads(client.requests[1]["messages"][-1]["content"][0]["content"])
    assert "untrusted_page_content" in result


# --- fetch_page safety (real function) --------------------------------------------------


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost:8000/docs", "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/", "file:///etc/passwd", "ftp://example.com/", "https://example.com:8443/",
])
def test_fetch_page_refuses_internal_and_odd_urls(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.undo()  # use the real fetch_page
    assert "error" in fetch_page(url)
