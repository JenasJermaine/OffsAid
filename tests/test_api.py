from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.hashing import hash_lookup_value
from app.models import LookupLog
from app.normalize import normalize_name
from app.schemas import DISCLAIMER


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_agent_check_requires_an_identifier(client: TestClient) -> None:
    response = client.post("/v1/agent-check", json={"claimed_country": "Kenya"})
    assert response.status_code == 422


def test_agent_check_found_includes_country_status_and_disclaimer(
    client: TestClient,
) -> None:
    response = client.post("/v1/agent-check", json={"name": "James Otieno"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "found"
    assert body["match"]["country"] == "Kenya"
    assert body["match"]["licence_status"] == "licensed"
    assert body["disclaimer"] == DISCLAIMER


def test_agent_check_not_found(client: TestClient) -> None:
    response = client.post("/v1/agent-check", json={"name": "Nobody Fictional"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_found"
    assert body["match"] is None
    assert body["disclaimer"] == DISCLAIMER


def test_agent_check_writes_hashed_log(client: TestClient, db: Session) -> None:
    response = client.post("/v1/agent-check", json={"name": "James Otieno"})
    assert response.status_code == 200
    logs = db.query(LookupLog).all()
    assert len(logs) == 1
    assert logs[0].query_hash == hash_lookup_value(normalize_name("James Otieno"))
    raw = str(logs[0].query_hash)
    assert "James" not in raw
    assert "Otieno" not in raw
