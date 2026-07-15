from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+psycopg://baton:baton@localhost:5433/baton"
    JWT_SECRET: str = "change-me-in-production"
    ACCESS_TOKEN_TTL_MIN: int = 30
    REFRESH_TOKEN_TTL_DAYS: int = 14
    SET_PASSWORD_TOKEN_TTL_HOURS: int = 72
    FRONTEND_ORIGIN: str = "http://localhost:5173"
    EMAIL_CONN: str = ""  # Azure Communication Services connection string; empty = console dev mode
    EMAIL_FROM: str = "DoNotReply@baton.local"
    AZURE_BLOB_CONN: str = ""  # empty = local filesystem fallback (FILES_DIR)
    FILES_DIR: str = "var/files"
    ANTHROPIC_API_KEY: str = ""  # server-side only; empty = payment-terms polish disabled
    SCHEDULER_ENABLED: bool = True  # daily 07:00 Asia/Dubai digest (off in tests)
    # Upload limits (uploads.py). Every one is a rejection threshold, so 0 = unlimited and
    # the defaults are what production runs on unless an env var says otherwise.
    MAX_UPLOAD_MB: int = 10  # per file, enforced before the bytes are materialized
    TENANT_STORAGE_QUOTA_MB: int = 2048  # total stored bytes per firm
    DEMO_STORAGE_QUOTA_MB: int = 200  # tighter, because the demo firm's credentials are public


@lru_cache
def get_settings() -> Settings:
    return Settings()
