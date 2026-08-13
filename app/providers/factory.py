from __future__ import annotations

from app.providers.google_flow.provider import GoogleFlowProvider
from app.providers.registry import ProviderRegistry


def build_provider_registry(*, bridge, asset_service, extra_providers=None) -> ProviderRegistry:
    """Compose configured adapters in one place.

    New production providers are added to this composition root; API routes,
    workers, repositories, and storage remain unchanged.
    """

    registry=ProviderRegistry()
    registry.register(GoogleFlowProvider(bridge,asset_service))
    for provider in extra_providers or []:
        registry.register(provider)
    return registry
