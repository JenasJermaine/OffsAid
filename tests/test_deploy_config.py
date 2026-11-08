from app.config import Settings
from app.scam_runtime import RemoteScamLanguageChecker


def test_remote_checker_accepts_url_with_classify_suffix(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"flags": [], "messages_checked": 1, "mode": "model+rules"}

    def fake_post(url, **kwargs):
        seen["url"] = url
        return Response()

    monkeypatch.setattr("app.scam_runtime.httpx.post", fake_post)
    checker = RemoteScamLanguageChecker("https://example.modal.run/classify/", "t", 5)
    checker.check_chat([{"sender": "agent", "text": "hello"}])
    assert seen["url"] == "https://example.modal.run/classify"


def test_remote_checker_falls_back_on_invalid_response(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"unexpected": True}

    monkeypatch.setattr("app.scam_runtime.httpx.post", lambda url, **kw: Response())
    checker = RemoteScamLanguageChecker("https://example.modal.run", "t", 5)
    result = checker.check_chat([{"sender": "agent", "text": "Pay the clearance fee today."}])
    assert result["mode"] == "rules_only"


def test_database_url_from_render_uses_psycopg3():
    for url in ("postgres://u:p@h:5432/d", "postgresql://u:p@h:5432/d"):
        assert Settings(database_url=url).database_url == "postgresql+psycopg://u:p@h:5432/d"
    assert Settings(database_url="sqlite:///./x.db").database_url == "sqlite:///./x.db"
