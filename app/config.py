import re

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("database_url")
    @classmethod
    def _use_psycopg3(cls, url: str) -> str:
        # Render and most hosts hand out postgres:// or postgresql://, which SQLAlchemy maps to
        # psycopg2 (not installed). Use the psycopg 3 driver from requirements.txt instead.
        return re.sub(r"^postgres(?:ql)?://", "postgresql+psycopg://", url)

    # Database
    database_url: str = "postgresql+psycopg://offsaid:offsaid@localhost:5432/offsaid"
    lookup_hash_salt: str = "change-me-in-production"

    # Agent check
    found_min_score: int = 92
    possible_min_score: int = 80
    seed_csv_path: str = "data/licensed_agents.simulated.csv"
    windows_csv_path: str = "data/registration_windows.simulated.csv"

    # WhatsApp (Meta Cloud API)
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_api_version: str = "v23.0"
    whatsapp_graph_url: str = "https://graph.facebook.com"
    max_media_bytes: int = 20 * 1024 * 1024

    # Twilio WhatsApp. Empty auth token = replies are logged, not sent.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""
    twilio_api_base: str = "https://api.twilio.com"

    # LLM (Claude). Empty key = LLM layers are skipped and rules-only checks run.
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"

    # Web-checking agent limits, per case
    web_agent_max_tool_calls: int = 12
    web_agent_timeout_seconds: int = 180
    # "low" gives fewer, more consolidated tool calls; raise it if checks miss evidence.
    web_agent_effort: str = "low"

    # Outbound lookups (RDAP, Wikidata). Off in tests.
    network_checks_enabled: bool = True
    rdap_base_url: str = "https://rdap.org/domain/"
    new_domain_days: int = 180
    # Wikimedia blocks requests whose User-Agent has no contact URL or email.
    lookup_user_agent: str = "OffsAid/0.1 (https://github.com/JenasJermaine/OffsAid)"

    # Scam-language classifier folder. Missing directory: rules_only mode (fee rule).
    offsaid_model_dir: str = "models/offsaid_classifier_v4"
    scam_model_api_url: str = ""
    scam_model_api_token: str = ""
    scam_model_timeout_seconds: int = 120

    # Cross-case linking. Matching thresholds live in app/case_linking.py only (untested starting points).
    # Secret for the HMAC of phone numbers, accounts and emails, at least 32 characters. Permanent:
    # changing or losing it makes every stored hash unmatchable (wipe the linking tables and re-seed).
    offsaid_hash_key: str = ""
    # Linking runs per WhatsApp user per hour, so the bot cannot be used to probe the database.
    linking_max_per_hour: int = 10

    # Browser demo of the WhatsApp chat at /demo (same pipeline as the webhooks). Turn off in production.
    demo_chat_enabled: bool = True

    # KEFWA review page (HTTP Basic). Empty password = page disabled.
    admin_username: str = "kefwa"
    admin_password: str = ""

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


settings = Settings()
