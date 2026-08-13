from __future__ import annotations

from dataclasses import dataclass

from app.extension.manager import ExtensionManager
from app.providers.google_flow.browser_bridge import FlowBridge


@dataclass
class Runtime:
    settings: object
    bridge: FlowBridge
    extension_manager: ExtensionManager


def build_runtime(settings) -> Runtime:
    bridge = FlowBridge(
        flow_api_key=settings.flow_api_key,
        slot_capacity=settings.account_slot_capacity,
        cooldown_seconds=settings.account_rate_limit_cooldown_seconds,
    )
    return Runtime(settings, bridge, ExtensionManager(bridge))
