from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_prefix="FLOW_PROVIDER_",env_file=".env",extra="ignore")

    env:Literal["development","test","production"]="development"
    database_url:str="sqlite:///./.data/flowprovider.db"
    bootstrap_api_key:str|None="fpa_dev_local"
    admin_api_key:str|None=None
    public_base_url:str="http://localhost:8000"
    worker_enabled:bool=True
    worker_poll_seconds:float=1.0
    worker_concurrency:int=Field(default=8,ge=1,le=64)
    worker_id:str="worker-1"
    lease_seconds:int=Field(default=120,ge=30)
    video_poll_seconds:int=Field(default=10,ge=0)
    max_attempts_before_dispatch:int=Field(default=5,ge=1,le=20)
    max_consecutive_poll_errors:int=Field(default=12,ge=1,le=100)
    max_provider_operation_seconds:int=Field(default=3600,ge=60,le=24*3600)

    local_storage_path:Path=Path(".data/assets")
    max_upload_bytes:int=Field(default=50*1024*1024,ge=1024,le=2*1024*1024*1024)
    max_reference_bytes:int=Field(default=25*1024*1024,ge=1024,le=512*1024*1024)
    max_reference_in_memory_bytes:int=Field(default=25*1024*1024,ge=1024,le=64*1024*1024)
    max_provider_output_bytes:int=Field(default=1024*1024*1024,ge=1024,le=4*1024*1024*1024)

    flow_api_key:str|None=None
    account_slot_capacity:int=Field(default=2,ge=1,le=8)
    account_rate_limit_cooldown_seconds:int=Field(default=180,ge=10)
    extension_heartbeat_seconds:int=Field(default=60,ge=5,le=120)
    extension_heartbeat_grace_seconds:int=Field(default=15,ge=5,le=120)

    @model_validator(mode="after")
    def validate_settings(self):
        if self.env!="test" and self.video_poll_seconds<1:
            raise ValueError("Video poll interval must be at least 1 second outside tests")
        if self.max_reference_bytes>self.max_upload_bytes:
            raise ValueError("Reference asset limit cannot exceed upload limit")
        if self.max_reference_in_memory_bytes>self.max_reference_bytes:
            raise ValueError("In-memory reference limit cannot exceed reference asset limit")
        if self.env=="production":
            if not self.database_url.startswith("postgresql"):
                raise ValueError("Production requires PostgreSQL")
            if self.bootstrap_api_key and (self.bootstrap_api_key.startswith("fpa_dev_") or "change_me" in self.bootstrap_api_key.lower()):
                raise ValueError("Disable bootstrap seeding or configure a non-development API key")
            parsed=urlparse(self.public_base_url)
            if parsed.scheme!="https" or not parsed.netloc:
                raise ValueError("Production public base URL must use HTTPS")
        return self


@lru_cache
def get_settings()->Settings:return Settings()
