from __future__ import annotations

from app.api.errors import APIError


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, object] = {}

    def register(self, provider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str):
        provider = self._providers.get(name)
        if provider is None:
            raise APIError(400, "UNSUPPORTED_PROVIDER", f"Provider '{name}' is not configured.", field="provider")
        return provider
