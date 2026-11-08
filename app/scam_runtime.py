"""Load the scam-language checker once, and run it on an extracted chat.

The orchestrator (WhatsApp) and the API routes call these functions. They do not
retrain, tune or edit the rules. Message text is not written to the log.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.chat_extraction import ChatShot, extract_chat, to_checker_input
from app.config import settings
from app.scam_language import ScamLanguageChecker, select_agent_messages

log = logging.getLogger(__name__)

_checker: ScamLanguageChecker | None = None

GROUP_NOTE = (
    "Several people appear to have written in this chat. The wording check is less reliable here."
)


class RemoteScamLanguageChecker(ScamLanguageChecker):
    def __init__(self, base_url: str, api_token: str, timeout: int) -> None:
        if urlparse(base_url).scheme != "https" or not urlparse(base_url).netloc:
            raise ValueError("SCAM_MODEL_API_URL must be an HTTPS URL.")
        # Accept the endpoint with or without the /classify suffix (the Modal URL is often copied with it).
        self.base_url = base_url.rstrip("/").removesuffix("/classify")
        self.api_token = api_token
        self.timeout = timeout
        self.mode = "model+rules"

    def check_chat(self, extraction) -> dict:
        messages = extraction.get("messages", []) if isinstance(extraction, dict) else extraction
        try:
            response = httpx.post(
                f"{self.base_url}/classify",
                json={"messages": messages},
                headers={"Authorization": f"Bearer {self.api_token}"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError):
            log.exception("Remote scam-language classifier failed; using the local fee rule only")
            return ScamLanguageChecker().check_chat(extraction)
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("flags"), list)
            or not isinstance(result.get("messages_checked"), int)
            or result.get("mode") != "model+rules"
        ):
            log.error("Remote scam-language classifier returned an invalid response; using the local fee rule only")
            return ScamLanguageChecker().check_chat(extraction)
        return result


def load_checker() -> ScamLanguageChecker:
    """Load the hosted classifier when configured, otherwise use local weights or rules."""
    if settings.scam_model_api_url:
        if not settings.scam_model_api_token:
            raise ValueError("SCAM_MODEL_API_TOKEN is required when SCAM_MODEL_API_URL is set.")
        log.info("Using remote scam-language classifier at %s", settings.scam_model_api_url)
        return RemoteScamLanguageChecker(
            settings.scam_model_api_url,
            settings.scam_model_api_token,
            settings.scam_model_timeout_seconds,
        )

    model_dir = settings.offsaid_model_dir
    if not Path(model_dir).is_dir():
        log.warning(
            "Scam-language model folder %s is missing; running in rules_only mode", model_dir
        )
        return ScamLanguageChecker()
    try:
        checker = ScamLanguageChecker.load(model_dir)
    except Exception:
        log.exception("Model failed to load; scam-language check running in rules_only mode")
        return ScamLanguageChecker()
    try:
        checker.classify([{"sender": "agent", "text": "warmup"}])
    except Exception:
        log.exception("Model warmup failed; scam-language check running in rules_only mode")
        return ScamLanguageChecker()
    log.info("Scam-language model loaded from %s (mode=%s)", model_dir, checker.mode)
    return checker


def get_scam_checker() -> ScamLanguageChecker:
    """The process-wide checker. The FastAPI lifespan calls this at startup."""
    global _checker
    if _checker is None:
        _checker = load_checker()
    return _checker


def run_scam_language_check(checker: ScamLanguageChecker, extraction) -> dict:
    """Classify one extracted chat. Only the other party's messages are scored.

    extraction is a ChatShot, the dict from to_checker_input, or a list of messages.
    """
    if isinstance(extraction, ChatShot):
        extraction = to_checker_input(extraction)
    return checker.check_chat(extraction)


def check_screenshot(data: bytes, mime_type: str) -> dict:
    """Extract a screenshot, then classify the other party's text.

    When the vision model is unavailable the result says so (unavailable=True)
    and does not invent messages. mode stays visible so a rules-only run is not
    presented as a full check.
    """
    read = extract_chat(data, mime_type)
    if read.unavailable or read.shot is None:
        result = {
            "flags": [],
            "messages_checked": 0,
            "mode": get_scam_checker().mode,
            "agent_messages": [],
            "app": "other",
            "unavailable": True,
            "note": None,
        }
    else:
        prepared = to_checker_input(read.shot)
        result = run_scam_language_check(get_scam_checker(), prepared)
        result["agent_messages"] = select_agent_messages(prepared["messages"])
        result["app"] = prepared["app"]
        result["unavailable"] = False
        result["note"] = GROUP_NOTE if prepared["group_chat"] else None
    log.info(
        "scam-language check mode=%s messages_checked=%s labels=%s unavailable=%s",
        result["mode"],
        result["messages_checked"],
        [flag["label"] for flag in result["flags"]],
        result["unavailable"],
    )
    return result
