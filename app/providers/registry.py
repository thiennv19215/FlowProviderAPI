from __future__ import annotations

import re

from app.api.errors import APIError
from app.providers.base import provider_capabilities


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, object] = {}

    def register(self, provider) -> None:
        name=getattr(provider,"name",None)
        if not isinstance(name,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",name):
            raise ValueError("Provider name must contain only letters, numbers, underscores, or hyphens.")
        if name in self._providers:
            raise ValueError(f"Provider '{name}' is already registered.")
        capabilities=provider_capabilities(provider)
        if not any((capabilities.image,capabilities.video,capabilities.omni)):
            raise ValueError(f"Provider '{name}' does not declare any generation capability.")
        self._providers[name] = provider

    def get(self, name: str):
        provider = self._providers.get(name)
        if provider is None:
            raise APIError(400, "UNSUPPORTED_PROVIDER", f"Provider '{name}' is not configured.", field="provider")
        return provider

    def supports(self, name: str, kind: str) -> bool:
        return provider_capabilities(self.get(name)).supports(kind)

    def names(self) -> tuple[str, ...]:
        return tuple(self._providers)
