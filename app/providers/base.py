from __future__ import annotations

from dataclasses import dataclass, field


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