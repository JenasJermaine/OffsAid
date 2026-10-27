from sqlalchemy.orm import Session

from app.hashing import hash_lookup_value
from app.matching import AgentQuery, verify_agent
from app.models import LicensedAgent, LookupLog
from app.normalize import normalize_name


def _agents(db: Session) -> list[LicensedAgent]:
    return db.query(LicensedAgent).all()


def test_swapped_first_and_last_name_is_found(db: Session) -> None:
    outcome = verify_agent(AgentQuery(name="Otieno James"), _agents(db))
    assert outcome.status == "found"
    assert outcome.agent is not None
    assert outcome.agent.name == "James Otieno"


def test_typo_is_possible_match_not_confirmed(db: Session) -> None:
    outcome = verify_agent(AgentQuery(name="James Oteino"), _agents(db))
    assert outcome.status == "possible_match"
    assert outcome.agent is not None
    assert outcome.agent.name == "James Otieno"
    assert outcome.score is not None
    assert outcome.score < 92


def test_same_name_different_country_is_possible_match(db: Session) -> None:
    outcome = verify_agent(
        AgentQuery(name="Amina Wanjiku", claimed_country="Spain"),
        _agents(db),
    )
    assert outcome.status == "possible_match"
    assert outcome.agent is not None
    assert outcome.agent.country in {"Kenya", "England"}
    assert "different country" in outcome.reason.lower() or "check the details" in outcome.reason.lower()


def test_licence_hit_is_found(db: Session) -> None:
    outcome = verify_agent(AgentQuery(licence_number="KEFWA-SIM-002"), _agents(db))
    assert outcome.status == "found"
    assert outcome.agent is not None
    assert outcome.agent.name == "James Otieno"
    assert outcome.agent.licence_status == "licensed"


def test_unknown_name_is_not_found(db: Session) -> None:
    outcome = verify_agent(AgentQuery(name="Nobody Fictional"), _agents(db))
    assert outcome.status == "not_found"
    assert outcome.agent is None


def test_normalized_names_are_stored(db: Session) -> None:
    agent = db.query(LicensedAgent).filter(LicensedAgent.name == "James Otieno").one()
    assert agent.normalized_name == normalize_name("James Otieno")


def test_lookup_log_stores_hash_not_raw_values(db: Session) -> None:
    from app.main import _log_lookup
    from app.schemas import AgentCheckRequest

    request = AgentCheckRequest(name="James Otieno")
    outcome = verify_agent(AgentQuery(name="James Otieno"), _agents(db))
    _log_lookup(db, request, outcome)

    logs = db.query(LookupLog).all()
    assert len(logs) == 1
    log = logs[0]
    assert log.query_kind == "name"
    assert log.result_status == "found"
    assert log.query_hash == hash_lookup_value(normalize_name("James Otieno"))
    assert "james" not in log.query_hash.lower()
    assert "otieno" not in log.query_hash.lower()
