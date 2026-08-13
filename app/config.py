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
    caller_owned_allowed_hosts: str = ""
    max_reference_in_memory_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    max_provider_output_bytes: int = Field(default=1024 * 1024 * 1024, ge=1024, le=4 * 1024 * 1024 * 1024)
    max_provider_operation_seconds: int = Field(default=3600, ge=60, le=24 * 3600)
    video_poll_seconds: int = Field(default=10, ge=0)
    flow_api_key: str | None = None
    account_slot_capacity: int = Field(default=2, ge=1, le=8)
    account_rate_limit_cooldown_seconds: int = Field(default=180, ge=10)
    extension_heartbeat_seconds: int = Field(default=60, ge=5, le=120)
    extension_heartbeat_grace_seconds: int = Field(default=15, ge=5, le=120)

    @model_validator(mode="after")
    def validate_settings(self):
        if self.env != "test" and self.video_poll_seconds < 1:
            raise ValueError("Video poll interval must be at least 1 second outside tests")
        if self.env == "production":
            if not self.bootstrap_api_key:
                raise ValueError("Production requires a gateway API key")
            if self.bootstrap_api_key.startswith("fpa_dev_") or "change_me" in self.bootstrap_api_key.lower():
                raise ValueError("Production requires a non-development API key")
            if not self.caller_owned_allowed_hosts.strip():
                raise ValueError("Production requires caller-owned allowed hosts")
            parsed = urlparse(self.public_base_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("Production public base URL must use HTTPS")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
