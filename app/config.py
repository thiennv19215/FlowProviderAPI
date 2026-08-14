from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLOW_PROVIDER_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    public_base_url: str = "http://localhost:8000"
    bootstrap_api_key: str | None = "fpa_dev_local"
    flow_api_key: str | None = None
    project_store_path: str = ".data/projects.db"
    account_slot_capacity: int = Field(default=3, ge=1, le=3)
    account_rate_limit_cooldown_seconds: int = Field(default=180, ge=10)
    extension_heartbeat_seconds: int = Field(default=60, ge=5, le=120)
    extension_heartbeat_grace_seconds: int = Field(default=15, ge=5, le=120)

    @model_validator(mode="after")
    def validate_settings(self):
        if self.env == "production":
            if not self.bootstrap_api_key:
                raise ValueError("Production requires a gateway API key")
            if self.bootstrap_api_key.startswith("fpa_dev_") or "change_me" in self.bootstrap_api_key.lower():
                raise ValueError("Production requires a non-development API key")
            parsed = urlparse(self.public_base_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("Production public base URL must use HTTPS")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
