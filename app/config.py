import sys

from pydantic import ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    bot_username: str  # no leading @, used to build https://t.me/<bot_username> deep links
    admin_id: int
    database_url: str | None = None  # built from db_* fields below if not set directly
    stripe_secret_key: str | None = None  # stub for Epic 5, not used yet
    stripe_webhook_secret: str | None = None  # stub for Epic 5, not used yet

    db_user: str | None = None
    db_password: str | None = None
    db_name: str | None = None
    db_host: str = "db"  # matches the docker-compose service name; override for local host testing
    db_port: int = 5432

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
