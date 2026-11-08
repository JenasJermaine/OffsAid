"""Tests for cross-case linking.

Pure tests (no database) always run. Database tests need a Postgres 14+ with pg_trgm and run only when
OFFSAID_TEST_DB holds a DSN for an EMPTY, disposable database, for example:
    export OFFSAID_TEST_DB="postgresql://user@localhost:5432/offsaid_test"
    export OFFSAID_HASH_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
    pytest tests/test_case_linking.py -v
The DB fixture DROPS and recreates the three linking tables. Never point it at a real database.

Repo placement: only the imports and the schema path differ from the staged file; the tests are unchanged.
"""
import os
from pathlib import Path

import pytest

from app import case_linking as cl
from scripts import make_seed_data as seeds

KEY = b"k" * 32
SCHEMA = Path(__file__).resolve().parent.parent / "migrations" / "001_case_linking.sql"


# ------------------------------------------------------------------ pure tests
def test_phone_formats_normalise_to_one_value():
    forms = ["0712 345 678", "+254712345678", "254712345678", "0712-345-678", "+254 712 345 678"]
    assert len({cl.normalise_phone(f) for f in forms}) == 1
    assert cl.normalise_phone("0712345678") == "+254712345678"


def test_mobile_number_given_as_account_is_a_phone():
    assert cl.canonical_identifier("account", "0712 345 678") == ("phone", "+254712345678")
    assert cl.canonical_identifier("account", "9990001") == ("account", "9990001")


def test_email_and_domain_normalising():
    assert cl.normalise_email("M.Wende+x@Gmail.com") == "mwende@gmail.com"
    assert cl.normalise_domain("https://www.Club-FC.org/contact") == "club-fc.org"
    assert cl.normalise_domain("gmail.com") is None          # free mail is never a domain identifier
    case = cl.CaseInput(identifiers=[{"kind": "email", "value": "a@club-fc.org"}, {"kind": "email", "value": "b@gmail.com"}])
    kinds = {(k, v) for k, v, _ in cl.canonical_identifiers(case)}
    assert ("domain", "club-fc.org") in kinds and not any(k == "domain" and v == "gmail.com" for k, v in kinds)


def test_hash_depends_on_key_and_kind():
    a = cl.hash_identifier("phone", "+254712345678", KEY)
    assert a == cl.hash_identifier("phone", "+254712345678", KEY)
    assert a != cl.hash_identifier("phone", "+254712345678", b"z" * 32)
    assert a != cl.hash_identifier("account", "+254712345678", KEY)
    assert "712345678" not in a


def test_hash_key_is_required(monkeypatch):
    monkeypatch.delenv("OFFSAID_HASH_KEY", raising=False)
    with pytest.raises(RuntimeError):
        cl.get_hash_key()
    monkeypatch.setenv("OFFSAID_HASH_KEY", "short")
    with pytest.raises(RuntimeError):
        cl.get_hash_key()


def test_masking_makes_swapped_letters_identical():
    a = seeds.letter_text("L2", "Coast City FC", "Mr Kamau", player="John Doe", date="12 March 2026", amt="15,000")
    b = seeds.letter_text("L2", "Savannah Athletic", "Ms Otieno", player="Peter Ouma", date="3 May 2026", amt="20,000")
    ma = cl.mask_text(a, "Coast City FC", ["Mr Kamau", "John Doe"])
    mb = cl.mask_text(b, "Savannah Athletic", ["Ms Otieno", "Peter Ouma"])
    assert ma == mb
    for leak in ("coast", "savannah", "kamau", "15", "march"):
        assert leak not in ma


def test_masking_removes_contacts():
    m = cl.mask_text("Send KES 15,000 to 0712 345 678 or mail x@y.com or www.club.com now", None, None)
    assert "0712" not in m and "15" not in m and "@" not in m and "club.com" not in m


def test_club_name_comparison():
    assert cl.same_name("Coast City FC", "Coast City", club=True)
    assert cl.same_name("Lakeview United", "Lakeveiw United", club=True)        # typo
    assert not cl.same_name("Coast City FC", "Lakeview United", club=True)
    assert cl.count_distinct_names(["Coast City FC", "coast city", None, "Lakeview United"], club=True) == 2


def test_layouts_are_separated_from_each_other_in_hash_space(tmp_path):
    hashes = {}
    for layout in seeds.LAYOUTS:
        for club in ("Coast City FC", "Savannah Athletic"):
            p = tmp_path / f"{layout}-{club[:3]}.png"
            seeds.render_letter(layout, club, p)
            hashes[(layout, club)] = cl.image_phash(p)
    dist = lambda a, b: bin((a ^ b) & ((1 << 64) - 1)).count("1")
    for layout in seeds.LAYOUTS:
        assert dist(hashes[(layout, "Coast City FC")], hashes[(layout, "Savannah Athletic")]) <= cl.PHASH_MAX_DISTANCE
    ls = list(seeds.LAYOUTS)
    for i, a in enumerate(ls):
        for b in ls[i + 1:]:
            assert dist(hashes[(a, "Coast City FC")], hashes[(b, "Coast City FC")]) > cl.PHASH_MAX_DISTANCE


# ------------------------------------------------------------------ database tests
DSN = os.environ.get("OFFSAID_TEST_DB")
needs_db = pytest.mark.skipif(not DSN, reason="set OFFSAID_TEST_DB to run the database tests")


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    import psycopg
    monkeypatch.setenv("OFFSAID_HASH_KEY", KEY.decode())
    from scripts.seed_linking import load_seed_file
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("drop table if exists documents, identifiers, cases cascade")
        c.execute(SCHEMA.read_text())
        seeds.main(str(tmp_path / "seed"))
        assert load_seed_file(c, str(tmp_path / "seed" / "seed_cases.json")) == 10
        c.tmp = tmp_path
        yield c


def new_case(**kw):
    base = dict(source="live", claimed_club="Lakeview United", claimed_agent="Ms Atieno Otieno")
    base.update(kw)
    return cl.CaseInput(**base)


def phone(v):
    return {"kind": "phone", "value": v, "role": "agent_contact"}


def link_of(result, kind):
    found = [l for l in result["links"] if l["type"] == kind]
    return found[0] if found else None


@needs_db
def test_scenario1_same_number_under_a_different_club(conn):
    r = cl.process_case(conn, new_case(identifiers=[phone("+254 999 000 001")]))
    l = link_of(r, "phone")
    assert l["earlier_cases"] == 1 and l["different_club"] and l["flag"] and not l["known_contact"]
    assert r["escalate"] and "phone appears under different club names" in r["escalation_reasons"]
    assert "different club name" in l["summary"] and "None of them has been reviewed" in l["summary"]


@needs_db
def test_scenario2_same_payment_account(conn):
    r = cl.process_case(conn, new_case(claimed_club="Nyanza Rovers",
                        identifiers=[phone("+254 999 111 222"), {"kind": "account", "value": "9990002", "role": "payment_to"}]))
    l = link_of(r, "account")
    assert l and l["different_club"] and r["escalate"]
    assert link_of(r, "phone") is None


@needs_db
def test_scenario3_repurposed_letter(conn):
    p = conn.tmp / "new_letter.png"
    seeds.render_letter("L2", "Savannah Athletic", p)
    text = seeds.letter_text("L2", "Savannah Athletic", "Ms Otieno", player="Peter Ouma", date="3 May 2026", amt="20,000")
    r = cl.process_case(conn, new_case(claimed_club="Savannah Athletic", claimed_agent="Ms Otieno",
                        identifiers=[phone("+254 999 555 111")], letter_text=text, letter_phash=cl.image_phash(p)))
    l = link_of(r, "letter_template")
    assert l and l["different_club"] and l["flag"] and r["escalate"]
    assert "different club name" in l["summary"]


@needs_db
def test_same_layout_but_different_wording_is_not_a_template_match(conn):
    p = conn.tmp / "l3.png"
    seeds.render_letter("L3", "Savannah Athletic", p)
    other_text = seeds.letter_text("L1", "Savannah Athletic", "Ms Otieno")          # a different letter's wording
    r = cl.find_links(conn, new_case(claimed_club="Savannah Athletic", letter_text=other_text, letter_phash=cl.image_phash(p)))
    assert link_of(r, "letter_template") is None


@needs_db
def test_same_club_reusing_its_own_letter_is_not_flagged(conn):
    p = conn.tmp / "own.png"
    seeds.render_letter("L5", "Riverbend Rovers", p)
    text = seeds.letter_text("L5", "Riverbend Rovers", "Ms Wafula", player="Another Player", date="9 June 2026")
    r = cl.find_links(conn, new_case(claimed_club="Riverbend Rovers FC", claimed_agent="Ms Wafula",
                      letter_text=text, letter_phash=cl.image_phash(p)))
    l = link_of(r, "letter_template")
    assert l and not l["different_club"] and not l["flag"] and not r["escalate"]


@needs_db
def test_scenario4_same_wording_from_a_different_number(conn):
    wording = ("Great news. The coach has already picked you. Send the registration fee to my mpesa number before Monday and we "
               "book your ticket. Keep this between us.")
    r = cl.find_links(conn, new_case(identifiers=[phone("+254 999 777 888")], wording_text=wording,
                      wording_labels=["advance_fee_request", "guaranteed_outcome", "secrecy"]))
    l = link_of(r, "wording")
    assert l and l["earlier_cases"] >= 2 and l["flag"] and l["statuses"].get("confirmed_scam") == 1
    assert link_of(r, "phone") is None and not r["escalate"]          # wording alone never escalates


@needs_db
def test_scenario5_legitimate_repeat_is_known_contact_not_a_flag(conn):
    r = cl.process_case(conn, new_case(claimed_club="Riverbend Rovers FC", claimed_agent="Ms Wafula",
                        identifiers=[phone("0999 000 005")]))
    l = link_of(r, "phone")
    assert l["known_contact"] and not l["flag"] and not r["escalate"]
    assert "Known contact" in l["summary"]


@needs_db
def test_scenario6_match_with_a_confirmed_scam_escalates_even_with_the_same_club(conn):
    r = cl.process_case(conn, new_case(claimed_club="Gold Coast Academy", claimed_agent="Mr Otieno",
                        identifiers=[phone("0999 000 006")]))
    l = link_of(r, "phone")
    assert l["flag"] and l["involves_escalation_status"] and r["escalate"]
    assert "KEFWA has confirmed 1 of them as fraud" in l["summary"]


@needs_db
def test_scenario7_no_match(conn):
    r = cl.process_case(conn, new_case(identifiers=[phone("+254 999 424 242"), {"kind": "email", "value": "z@fresh-club.example"}]))
    assert r["links"] == [] and not r["escalate"]
    assert "does not mean the offer is safe" in cl.player_view(r)["no_match_note"]


@needs_db
def test_payment_number_matches_an_earlier_contact_number(conn):
    r = cl.find_links(conn, new_case(identifiers=[{"kind": "account", "value": "+254 999 000 003", "role": "payment_to"}]))
    assert link_of(r, "phone") and link_of(r, "phone")["earlier_cases"] == 1


@needs_db
def test_email_matches_ignore_gmail_dots_and_tags(conn):
    r = cl.find_links(conn, new_case(identifiers=[{"kind": "email", "value": "m.wende.fc+trial@gmail.com"}]))
    assert link_of(r, "email") and link_of(r, "domain") is None


@needs_db
def test_sender_domain_match(conn):
    r = cl.find_links(conn, new_case(identifiers=[{"kind": "email", "value": "info@coastcity-fc.example"}]))
    l = link_of(r, "domain")
    assert l and l["different_club"] and l["strength"] == "medium"


@needs_db
def test_retried_webhook_is_idempotent_and_never_matches_itself(conn):
    case = new_case(external_ref="wamid.ABC123", identifiers=[phone("+254 999 313 131")])
    first = cl.process_case(conn, case)
    second = cl.process_case(conn, case)
    assert first["case_id"] == second["case_id"] and second["links"] == []
    assert conn.execute("select count(*) from cases where external_ref = 'wamid.ABC123'").fetchone()[0] == 1


@needs_db
def test_second_live_case_links_to_the_first(conn):
    cl.process_case(conn, new_case(external_ref="m1", claimed_club="Alpha FC", identifiers=[phone("+254 999 600 600")]))
    r = cl.process_case(conn, new_case(external_ref="m2", claimed_club="Beta United", identifiers=[phone("0999 600 600")]))
    assert link_of(r, "phone")["different_club"] and r["escalate"]


@needs_db
def test_player_view_hides_case_details_and_raw_values(conn):
    r = cl.process_case(conn, new_case(identifiers=[phone("+254 999 000 001")]))
    view = cl.player_view(r)
    flat = str(view)
    assert "case_ids" not in flat and "first_seen" not in flat and "999000001" not in flat.replace(" ", "")
    assert view["links"][0]["summary"] and view["escalated"] is True


@needs_db
def test_nothing_raw_is_stored(conn):
    cl.process_case(conn, new_case(external_ref="raw?", identifiers=[phone("+254 999 909 090"),
                    {"kind": "email", "value": "secret@fresh.example"}], wording_text="Send KES 5,000 to 0999 909 090 now"))
    dump = " ".join(str(r) for t in ("cases", "identifiers", "documents") for r in conn.execute(f"select * from {t}").fetchall())
    for raw in ("909090", "909 090", "secret@fresh", "0999 909"):
        assert raw not in dump


@needs_db
def test_status_change_and_erasure(conn):
    r = cl.process_case(conn, new_case(external_ref="s1", identifiers=[phone("+254 999 808 080")]))
    cl.set_status(conn, r["case_id"], "confirmed_scam")
    again = cl.find_links(conn, new_case(claimed_club="Other FC", identifiers=[phone("0999 808 080")]))
    assert again["escalate"] and "confirmed scam status" in " ".join(again["escalation_reasons"])
    cl.delete_case(conn, r["case_id"])
    assert cl.find_links(conn, new_case(identifiers=[phone("0999 808 080")]))["links"] == []
    assert conn.execute("select count(*) from identifiers where case_id = %s", (r["case_id"],)).fetchone()[0] == 0
