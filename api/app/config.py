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


@lru_cache
def get_settings() -> Settings:
    return Settings()
