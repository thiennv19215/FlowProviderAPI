from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLOW_PROVIDER_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./.data/flowprovider.db"
    bootstrap_api_key: str | None = "fpa_dev_local"
    public_base_url: str = "http://localhost:8000"
    worker_enabled: bool = True
    worker_poll_seconds: float = 1.0
    worker_id: str = "worker-1"
    lease_seconds: int = 120
    video_poll_seconds: int = 10
    max_attempts_before_dispatch: int = 5

    storage_backend: Literal["local", "r2"] = "local"
    local_storage_path: Path = Path(".data/assets")
    r2_endpoint_url: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str | None = None
    r2_region: str = "auto"
    asset_url_ttl_seconds: int = 1800

    flow_api_key: str = "AIzaSyBtrm0o5ab1c-Ec8ZuLcGt3oJAA5VWt3pY"
    account_slot_capacity: int = Field(default=2, ge=1, le=8)
    account_rate_limit_cooldown_seconds: int = 180

    @model_validator(mode="after")
    def validate_production(self):
        if self.env == "production" and self.bootstrap_api_key == "fpa_dev_local":
            raise ValueError("Set a production bootstrap API key or disable bootstrap seeding")
        if self.storage_backend == "r2":
            required = [self.r2_endpoint_url, self.r2_access_key_id, self.r2_secret_access_key, self.r2_bucket]
            if not all(required):
                raise ValueError("R2 storage requires endpoint, access key, secret key, and bucket")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
