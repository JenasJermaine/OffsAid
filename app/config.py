from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://offsaid:offsaid@localhost:5432/offsaid"
    lookup_hash_salt: str = "change-me-in-production"
    found_min_score: int = 92
    possible_min_score: int = 80
    seed_csv_path: str = "data/licensed_agents.simulated.csv"


settings = Settings()
