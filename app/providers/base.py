from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class ProviderError(RuntimeError):
    def __init__(self,code:str,message:str,*,status_code:int|None=None,retryable:bool=False,details:list[dict[str,str|None]]|None=None):
        super().__init__(message);self.code=code;self.message=message;self.status_code=status_code;self.retryable=retryable;self.details=details or []


@dataclass
class ProviderMedia:
    media_id: str | None = None
    url: str | None = None
    thumbnail_url: str | None = None
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


@dataclass(frozen=True)
class ProviderCapabilities:
    """Operations exposed by a provider adapter.

    ``account_pool`` describes an implementation detail used by admission
    checks. Account selection and quota management still belong to the
    provider itself.
    """

    image: bool = False
    video: bool = False
    omni: bool = False
    account_pool: bool = False

    def supports(self, kind: str) -> bool:
        return bool(getattr(self, kind, False))


@dataclass(frozen=True)
class ProviderContext:
    """Opaque execution context prepared by a provider before dispatch."""

    account_id: str | None = None


@runtime_checkable
class MediaProvider(Protocol):
    """Stable boundary between orchestration and upstream media providers."""

    name: str
    capabilities: ProviderCapabilities

    async def prepare(self, *, job, db) -> ProviderContext: ...

    async def generate_image(self, *, job, db, context: ProviderContext) -> list[ProviderMedia]: ...

    async def dispatch_video(self, *, job, db, context: ProviderContext) -> ProviderDispatch: ...

    async def dispatch_omni(self, *, job, db, context: ProviderContext) -> ProviderDispatch: ...

    async def poll(self, *, job, db, context: ProviderContext, dispatch: ProviderDispatch) -> ProviderPollResult: ...


def provider_capabilities(provider) -> ProviderCapabilities:
    """Return declared capabilities, adapting V1 providers during migration."""

    declared = getattr(provider, "capabilities", None)
    if isinstance(declared, ProviderCapabilities):
        return declared
    return ProviderCapabilities(
        image=callable(getattr(provider, "generate_image", None)),
        video=callable(getattr(provider, "dispatch_video", None)),
        omni=callable(getattr(provider, "dispatch_omni", None)),
        account_pool=bool(getattr(provider, "requires_account_pool", False)),
    )
