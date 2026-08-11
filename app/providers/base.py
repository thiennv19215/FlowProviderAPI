from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ProviderMedia:
    media_id: str | None = None
    url: str | None = None
    mime_type: str | None = None
    bytes_data: bytes | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None


@dataclass
class ProviderDispatch:
    operation_ids: list[str]
    workflows: list[dict] = field(default_factory=list)


@dataclass
class ProviderPollResult:
    done: bool
    outputs: list[ProviderMedia] = field(default_factory=list)
    error: str | None = None


class ProviderAdapter(Protocol):
    name: str
    requires_account_pool: bool

    async def generate_image(self, *, job, db, account_id: str | None) -> list[ProviderMedia]: ...
    async def dispatch_video(self, *, job, db, account_id: str | None) -> ProviderDispatch: ...
    async def dispatch_omni(self, *, job, db, account_id: str | None) -> ProviderDispatch: ...
    async def poll_video(self, *, job, db, account_id: str | None, dispatch: ProviderDispatch) -> ProviderPollResult: ...
