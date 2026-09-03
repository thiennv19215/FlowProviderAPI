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
    extension_api_key: str | None = None
    flow_api_key: str | None = None
    project_store_path: str = ".data/projects.db"
    asset_store_path: str = ".data/assets"
    asset_retention_days: int = Field(default=30, ge=1, le=3650)
    account_slot_capacity: int = Field(default=3, ge=1, le=20)
    account_image_slot_capacity: int = Field(default=4, ge=1, le=20)
    account_video_slot_capacity: int = Field(default=3, ge=1, le=10)
    account_rate_limit_cooldown_seconds: int = Field(default=180, ge=10)
    extension_heartbeat_seconds: int = Field(default=60, ge=5, le=120)
    extension_heartbeat_grace_seconds: int = Field(default=15, ge=5, le=120)
    worker_enabled: bool = True
    worker_poll_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    worker_poll_max_backoff_seconds: int = Field(default=300, ge=10, le=3600)
    worker_poll_claim_lease_seconds: int = Field(default=120, ge=30, le=600)
    worker_dispatch_lease_seconds: int = Field(default=900, ge=60, le=3600)
    worker_running_timeout_seconds: int = Field(default=86400, ge=300, le=604800)
    worker_concurrency: int = Field(default=4, ge=1, le=16)

    @model_validator(mode="after")
    def validate_settings(self):
        if self.env == "production":
            if not self.extension_api_key:
                raise ValueError("Production requires a separate extension connector API key")
            if self.extension_api_key.startswith("fpe_dev_") or "change_me" in self.extension_api_key.lower():
                raise ValueError("Production requires a non-development extension connector API key")
            parsed = urlparse(self.public_base_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("Production public base URL must use HTTPS")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
