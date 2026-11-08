from app.scam_runtime import RemoteScamLanguageChecker, load_checker


def test_remote_checker_sends_messages_and_returns_classifier_result(monkeypatch):
    seen = {}
    expected = {
        "flags": [],
        "messages_checked": 1,
        "mode": "model+rules",
    }

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return expected

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return Response()

    monkeypatch.setattr("app.scam_runtime.httpx.post", fake_post)
    checker = RemoteScamLanguageChecker("https://example.modal.run", "test-token", 17)
    messages = [{"sender": "agent", "text": "Pay the fee today."}]

    assert checker.check_chat(messages) == expected
    assert seen["url"] == "https://example.modal.run/classify"
    assert seen["json"] == {"messages": messages}
    assert seen["headers"] == {"Authorization": "Bearer test-token"}
    assert seen["timeout"] == 17


def test_remote_checker_requires_https():
    try:
        RemoteScamLanguageChecker("http://example.modal.run", "test-token", 17)
    except ValueError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("Expected HTTP URLs to be rejected.")


def test_remote_checker_falls_back_to_local_fee_rule(monkeypatch):
    def fail_post(*args, **kwargs):
        import httpx

        raise httpx.ConnectError("unavailable")

    monkeypatch.setattr("app.scam_runtime.httpx.post", fail_post)
    checker = RemoteScamLanguageChecker("https://example.modal.run", "test-token", 17)

    result = checker.check_chat([{"sender": "agent", "text": "Pay the clearance fee today."}])

    assert result["mode"] == "rules_only"
    assert result["flags"][0]["label"] == "advance_fee_request"
    assert result["flags"][0]["source"] == "rule"


def test_remote_url_requires_api_token(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "scam_model_api_url", "https://example.modal.run")
    monkeypatch.setattr(settings, "scam_model_api_token", "")

    try:
        load_checker()
    except ValueError as exc:
        assert "SCAM_MODEL_API_TOKEN" in str(exc)
    else:
        raise AssertionError("Expected a missing token to be rejected.")
