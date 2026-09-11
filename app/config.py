import sys

from pydantic import ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    bot_username: str  # no leading @, used to build https://t.me/<bot_username> deep links
    admin_id: int
    database_url: str | None = None  # built from db_* fields below if not set directly
    stripe_secret_key: str | None = None  # stub for Epic 5, not used yet
    stripe_webhook_secret: str | None = None  # signs the Stripe webhook, see app/web/stripe_webhook.py

    # Stripe delivers webhooks over HTTP, so the bot serves a small aiohttp app
    # alongside polling. Host 0.0.0.0 so the port is reachable from outside the
    # container; the published port is set in docker-compose.yml.
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8000

    # Closed Telegram channel the bot invites paying subscribers to. Still
    # pending from Irina, so it is optional: when unset the invite step is
    # skipped and the rest of the payment flow runs unchanged.
    channel_id: int | str | None = None

    db_user: str | None = None
    db_password: str | None = None
    db_name: str | None = None
    db_host: str = "db"  # matches the docker-compose service name; override for local host testing
    db_port: int = 5432

    @field_validator("channel_id", "stripe_secret_key", "stripe_webhook_secret", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        """A key left empty in .env arrives as "", which is not the same as
        absent for these optional fields — normalise it to None."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _build_database_url(self) -> "Settings":
        if self.database_url is None and self.db_user and self.db_password and self.db_name:
            self.database_url = (
                f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}"
            )
        return self


try:
    settings = Settings()
except ValidationError as exc:
    sys.exit(f"Invalid configuration:\n{exc}")
