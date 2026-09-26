from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "github-intelligence-api"
    environment: str = "local"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/github_intelligence"
    redis_url: str = "redis://localhost:6379/0"

    # Read-only GitHub Personal Access Token. Unused until Phase 2.
    github_token: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
