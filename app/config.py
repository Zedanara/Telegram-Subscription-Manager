import sys

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    admin_id: int
    database_url: str | None = None  # stub for Epic 2, not used yet
    stripe_secret_key: str | None = None  # stub for Epic 5, not used yet
    stripe_webhook_secret: str | None = None  # stub for Epic 5, not used yet


try:
    settings = Settings()
except ValidationError as exc:
    sys.exit(f"Invalid configuration:\n{exc}")
