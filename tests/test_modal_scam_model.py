from fastapi.testclient import TestClient

from modal_scam_model import web


def test_classifier_requires_authorization_header(monkeypatch):
    monkeypatch.setenv("SCAM_MODEL_API_TOKEN", "expected-token")

    response = TestClient(web).post(
        "/classify",
        json={"messages": [{"sender": "agent", "text": "hello"}]},
    )

    assert response.status_code == 401
    assert "Authorization header is missing" in response.json()["detail"]


def test_classifier_explains_malformed_authorization_header(monkeypatch):
    monkeypatch.setenv("SCAM_MODEL_API_TOKEN", "expected-token")

    response = TestClient(web).post(
        "/classify",
        json={"messages": [{"sender": "agent", "text": "hello"}]},
        headers={"Authorization": "Token wrong-scheme"},
    )

    assert response.status_code == 401
    assert "Malformed Authorization header" in response.json()["detail"]


def test_classifier_explains_token_mismatch_without_echoing_token(monkeypatch):
    monkeypatch.setenv("SCAM_MODEL_API_TOKEN", "expected-token")

    response = TestClient(web).post(
        "/classify",
        json={"messages": [{"sender": "agent", "text": "hello"}]},
        headers={"Authorization": "Bearer wrong-token"},
    )

    detail = response.json()["detail"]
    assert response.status_code == 401
    assert "Bearer token did not match" in detail
    assert "redeploy" in detail
    assert "wrong-token" not in detail
    assert "expected-token" not in detail


def test_classifier_reports_missing_modal_secret(monkeypatch):
    monkeypatch.delenv("SCAM_MODEL_API_TOKEN", raising=False)

    response = TestClient(web).post(
        "/classify",
        json={"messages": [{"sender": "agent", "text": "hello"}]},
        headers={"Authorization": "Bearer supplied-token"},
    )

    assert response.status_code == 503
    assert "offsaid-scam-api" in response.json()["detail"]
    assert "SCAM_MODEL_API_TOKEN" in response.json()["detail"]
