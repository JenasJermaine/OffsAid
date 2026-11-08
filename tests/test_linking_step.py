"""Cross-case linking in the pipeline: CaseInput mapping, report section, failure policy, rate limit,
KEFWA queue and reviewer routes. The tests of the matching itself are in test_case_linking.py.

Database tests run only with OFFSAID_TEST_DB set to an EMPTY, disposable Postgres 14+ with pg_trgm.
Their fixture DROPS the linking tables. Never point it at a real database.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest

import app.admin as admin
import app.document_extraction as extraction
import app.linking_step as step
from app import case_linking as cl
from app import report
from app.config import settings
from app.document_schemas import Check, DocumentCheckResponse, DocumentFields, FeeRequest, LinkingKeys, PageInfo, Quoted

KEY = "k" * 32
PLAYER = "254700000001"
ROOT = Path(__file__).resolve().parents[1]


def q(value: str) -> Quoted:
    return Quoted(value=value, quote=value)


def doc_result(**fields) -> DocumentCheckResponse:
    base = dict(
        transcribed_text="Dear John Doe, Lakeview United invites you to a trial. Pay KES 15,000 to 0999 000 001.",
        document_type="trial_invitation",
        club_name=q("Lakeview United"),
        agent_name=q("Ms Otieno"),
        sender_email=q("otieno@lakeview.example"),
        contact_phones=[q("+254 999 000 001")],
        fee_requests=[FeeRequest(amount="15000", currency="KES", purpose="registration",
                                 account_or_number="9990002", quote="Pay KES 15,000")],
    )
    base.update(fields)
    return DocumentCheckResponse(
        extraction_method="vision_model",
        file=PageInfo(pages=1, has_text_layer=True),
        fields=DocumentFields(**base),
        checks=[Check(check="fee_or_payment_request", status="not_confirmed", strength="strong", red_flag=True, note="n")],
        summary={"consistent": 0, "not_confirmed": 1, "contradicted": 0, "red_flags": 1},
        needs_review=True,
        linking_keys=LinkingKeys(file_sha256="0" * 64),
    )


# ------------------------------------------------------------------ pure tests


def test_build_case_input_maps_the_other_partys_details():
    wording = {"agent_messages": [{"sender": "agent", "text": "Send the fee today."}, {"sender": "agent", "text": "Keep it secret."}],
               "flags": [{"label": "secrecy"}, {"label": "advance_fee_request"}]}
    case = step.build_case_input(doc_result(), wording, letter_phash=123, external_ref="wamid.1", player_number=PLAYER)
    assert case.source == "live" and case.external_ref == "wamid.1"
    assert case.claimed_club == "Lakeview United" and case.claimed_agent == "Ms Otieno"
    assert {(i["kind"], i["role"]) for i in case.identifiers} == {
        ("phone", "agent_contact"), ("account", "payment_to"), ("email", "sender")}
    assert case.letter_text and case.letter_phash == 123
    assert case.wording_text == "Send the fee today.\nKeep it secret."
    assert case.wording_labels == ["advance_fee_request", "secrecy"]
    assert "Ms Otieno" in case.mask_names
    assert PLAYER not in str(case.summary) and "999" not in str(case.summary)


def test_build_case_input_drops_the_players_own_number():
    result = doc_result(contact_phones=[q("0700 000 001"), q("+254 999 000 001")])
    case = step.build_case_input(result, player_number=PLAYER)
    assert [i["value"] for i in case.identifiers if i["kind"] == "phone"] == ["+254 999 000 001"]


def test_chat_screenshot_is_not_a_letter():
    case = step.build_case_input(doc_result(document_type="chat_screenshot"), letter_phash=5)
    assert case.letter_text is None and case.letter_phash is None


def test_report_section_shows_sentences_only_flagged_first():
    view = {"links": [{"type": "phone", "flag": False, "summary": "Known contact sentence."},
                      {"type": "account", "flag": True, "summary": "Flagged sentence."}],
            "no_match_note": None, "escalated": True, "simulated": True}
    text = report.linking_report(view)
    assert text.index("Flagged sentence.") < text.index("Known contact sentence.")
    assert report.LINKING_SIMULATED in text and report.LINKING_ESCALATED in text and report.LINKING_NOT_PROOF in text
    assert "%" not in text and "scammer" not in text.lower()


def test_report_section_without_links_unavailable_or_rate_limited():
    note = "No earlier case matched. Our database is small and new, so this does not mean the offer is safe."
    assert note in report.linking_report({"links": [], "no_match_note": note, "escalated": False})
    assert report.LINKING_UNAVAILABLE in report.linking_report(None)
    assert report.LINKING_RATE_LIMITED in report.linking_report({"rate_limited": True})


def test_linking_is_not_available_on_sqlite_and_never_raises(monkeypatch):
    monkeypatch.setattr(settings, "offsaid_hash_key", KEY)
    assert step.run_linking(doc_result(), None, b"", "application/pdf", "wamid.x", PLAYER) is None


def test_missing_hash_key_means_not_available(monkeypatch):
    monkeypatch.setattr(step, "is_postgres", lambda: True)
    monkeypatch.setattr(settings, "offsaid_hash_key", "short")
    assert step.run_linking(doc_result(), None, b"", "application/pdf", "wamid.x", PLAYER) is None


def test_rate_limiter_per_user_and_window():
    limiter = step.RateLimiter()
    assert all(limiter.allow("a", 2, now=t) for t in (0, 1))
    assert not limiter.allow("a", 2, now=2)
    assert limiter.allow("b", 2, now=2)
    assert limiter.allow("a", 2, now=3601)


def test_psycopg_dsn_strips_the_sqlalchemy_driver():
    assert step.psycopg_dsn("postgresql+psycopg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    assert step.psycopg_dsn("postgresql://u@h/db") == "postgresql://u@h/db"


def test_first_page_phash_matches_the_seed_hash_of_the_same_image(tmp_path):
    from scripts import make_seed_data as seeds

    path = tmp_path / "l1.png"
    seeds.render_letter("L1", "Coast City FC", path)
    assert step.first_page_phash(path.read_bytes(), "image/png") == cl.image_phash(path)


def test_admin_routes_are_off_without_a_password_and_need_login(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "")
    assert client.get("/admin/review-queue").status_code == 404
    monkeypatch.setattr(settings, "admin_password", "secret")
    assert client.get("/admin/review-queue").status_code == 401
    assert client.get("/admin/review-queue", auth=("kefwa", "wrong")).status_code == 401
    assert client.get("/admin/review-queue", auth=("kefwa", "secret")).status_code == 503   # SQLite in tests


# ------------------------------------------------------------------ database tests
DSN = os.environ.get("OFFSAID_TEST_DB")
needs_db = pytest.mark.skipif(not DSN, reason="set OFFSAID_TEST_DB to run the database tests")


@pytest.fixture()
def pg(tmp_path, monkeypatch):
    import psycopg

    from scripts import make_seed_data as seeds
    from scripts.migrate import apply_migrations
    from scripts.seed_linking import load_seed_file

    monkeypatch.setattr(settings, "offsaid_hash_key", KEY)
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("drop table if exists review_queue, documents, identifiers, cases, schema_migrations cascade")
        assert apply_migrations(c) == ["001_case_linking.sql", "002_review_queue.sql", "003_player_referrals.sql"]
        assert apply_migrations(c) == []
        seeds.main(str(tmp_path / "seed"))
        assert load_seed_file(c, str(tmp_path / "seed" / "seed_cases.json"), KEY.encode()) == 10

        @contextmanager
        def same_connection():
            yield c

        monkeypatch.setattr(step, "is_postgres", lambda: True)
        monkeypatch.setattr(step, "linking_connection", same_connection)
        monkeypatch.setattr(admin, "is_postgres", lambda: True)
        monkeypatch.setattr(admin, "linking_connection", same_connection)
        step.limiter = step.RateLimiter()
        yield c


def queue_rows(conn):
    return conn.execute("select case_id, reasons, linked_case_ids, status from review_queue").fetchall()


@needs_db
def test_demo_case_escalates_and_lands_in_the_kefwa_queue(pg):
    case = cl.CaseInput(external_ref="demo-1", claimed_club="Lakeview United", claimed_agent="Ms Otieno",
                        identifiers=[{"kind": "phone", "value": "+254 999 000 001", "role": "agent_contact"}])
    view = step.link_case(pg, case, KEY.encode())
    assert view["escalated"] and view["simulated"]
    assert view["links"][0]["summary"] == ("This phone number appeared in 1 earlier case, under a different club "
                                           "name than in this offer. None of them has been reviewed.")
    assert "case_ids" not in str(view) and "first_seen" not in str(view)
    sim01 = pg.execute("select id from cases where external_ref = 'seed:sim-01'").fetchone()[0]
    [(case_id, reasons, linked, status)] = queue_rows(pg)
    assert reasons == ["phone appears under different club names"] and linked == [sim01] and status == "open"

    again = step.link_case(pg, case, KEY.encode())                      # retried webhook
    assert again == view and len(queue_rows(pg)) == 1
    assert pg.execute("select count(*) from cases where external_ref = 'demo-1'").fetchone()[0] == 1


@needs_db
def test_no_match_is_not_queued(pg):
    case = cl.CaseInput(external_ref="demo-2", claimed_club="Lakeview United",
                        identifiers=[{"kind": "phone", "value": "+254 999 424 242"}])
    view = step.link_case(pg, case, KEY.encode())
    assert view["links"] == [] and not view["escalated"] and queue_rows(pg) == []
    assert "does not mean the offer is safe" in report.linking_report(view)


@needs_db
def test_run_linking_from_a_document_result_stores_nothing_raw(pg):
    result = doc_result(sender_email=q("secret.agent@fresh.example"), contact_phones=[q("+254 999 909 090")])
    view = step.run_linking(result, None, b"", "application/pdf", "wamid.raw", PLAYER)
    assert view is not None and not view.get("rate_limited")
    assert {l["type"] for l in view["links"]} >= {"account"}                 # 9990002 is sim-02's till
    dump = " ".join(str(r) for t in ("cases", "identifiers", "documents", "review_queue")
                    for r in pg.execute(f"select * from {t}").fetchall())
    for raw in ("909090", "909 090", "secret.agent", "fresh.example", "9990002", PLAYER, "John Doe", "Lakeview United invites"):
        assert raw not in dump


@needs_db
def test_rate_limit_stops_linking_for_that_user(pg, monkeypatch):
    monkeypatch.setattr(settings, "linking_max_per_hour", 1)
    first = step.run_linking(doc_result(), None, b"", "application/pdf", "wamid.r1", PLAYER)
    second = step.run_linking(doc_result(), None, b"", "application/pdf", "wamid.r2", PLAYER)
    assert "links" in first and second == {"rate_limited": True}
    assert pg.execute("select count(*) from cases where external_ref = 'wamid.r2'").fetchone()[0] == 0


@needs_db
def test_reviewer_sets_status_then_erases(pg, client, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "secret")
    auth = ("kefwa", "secret")
    step.link_case(pg, cl.CaseInput(external_ref="s1", claimed_club="Alpha FC",
                                    identifiers=[{"kind": "phone", "value": "+254 999 000 002"}]), KEY.encode())
    [item] = client.get("/admin/review-queue", auth=auth).json()
    assert item["reasons"] and item["linked_case_ids"] and item["source"] == "live"

    r = client.post(f"/admin/cases/{item['case_id']}/status", json={"status": "confirmed_scam", "note": "checked"}, auth=auth)
    assert r.status_code == 200 and client.get("/admin/review-queue", auth=auth).json() == []
    later = cl.find_links(pg, cl.CaseInput(claimed_club="Alpha FC", identifiers=[{"kind": "phone", "value": "0999 000 002"}]),
                          KEY.encode())
    assert later["escalate"] and "confirmed scam status" in " ".join(later["escalation_reasons"])
    assert client.post(f"/admin/cases/{item['case_id']}/status", json={"status": "fraud"}, auth=auth).status_code == 422

    assert client.delete(f"/admin/cases/{item['case_id']}", auth=auth).json() == {"case_id": item["case_id"], "deleted": True}
    assert queue_rows(pg) == []
    assert client.delete(f"/admin/cases/{item['case_id']}", auth=auth).status_code == 404


@needs_db
def test_whatsapp_webhook_end_to_end_second_offer_links_to_the_first(pg, client, monkeypatch):
    """A real test letter through the Meta webhook twice: the second report compares with the first."""
    import test_whatsapp_webhook as wa

    from app.whatsapp import Media

    fake = wa.FakeWhatsApp(Media((wa.LETTERS_DIR / wa.SCAM).read_bytes(), "application/pdf"))
    monkeypatch.setattr(wa.webhook, "get_whatsapp", lambda: fake)
    for name, value in (("whatsapp_token", "t"), ("whatsapp_app_secret", ""), ("network_checks_enabled", False),
                        ("anthropic_api_key", "")):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(extraction, "_ocr", lambda image: "")

    client.post("/webhook", json=wa._payload(wa._document("wamid.e2e1")))
    client.post("/webhook", json=wa._payload(wa._document("wamid.e2e2")))
    reports = [body for _, body in fake.sent if report.LINKING_HEADING in body]
    assert len(reports) == 2
    assert "earlier case" in reports[1] and report.LINKING_UNAVAILABLE not in reports[1]
    stored = pg.execute("select count(*) from cases where external_ref like 'wamid.e2e%'").fetchone()[0]
    assert stored == 2
    assert pg.execute("select count(*) from documents d join cases c on c.id = d.case_id "
                      "where c.external_ref = 'wamid.e2e1' and d.kind = 'letter' and d.phash is not null").fetchone()[0] == 1
