from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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

    # LLM (Claude). Empty key = LLM layers are skipped and rules-only checks run.
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"

    # Web-checking agent limits, per case
    web_agent_max_tool_calls: int = 12
    web_agent_timeout_seconds: int = 90

    # Outbound lookups (RDAP, Wikidata). Off in tests.
    network_checks_enabled: bool = True
    rdap_base_url: str = "https://rdap.org/domain/"
    new_domain_days: int = 180

    # Cross-case linking
    link_text_similarity: float = 0.85
    link_phash_max_distance: int = 6

    # KEFWA review page (HTTP Basic). Empty password = page disabled.
    admin_username: str = "kefwa"
    admin_password: str = ""

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


settings = Settings()
