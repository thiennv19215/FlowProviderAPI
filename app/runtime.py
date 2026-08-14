from __future__ import annotations

import asyncio
import weakref
from dataclasses import dataclass
from dataclasses import field

from app.extension.manager import ExtensionManager
from app.projects import ProjectStore
from app.providers.google_flow.browser_bridge import FlowBridge


@dataclass
class Runtime:
    settings: object
    bridge: FlowBridge
    extension_manager: ExtensionManager
    projects: ProjectStore
    project_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    media_locks: weakref.WeakValueDictionary = field(default_factory=weakref.WeakValueDictionary)
    active_jobs: dict[str, int] = field(default_factory=dict)
    reserved_credits: dict[str, int] = field(default_factory=dict)

    def connection_load(self, connection) -> int:
        return max(
            self.active_jobs.get(connection.id, 0),
            self.bridge.pending_count(connection.id),
        )

    def available_credits(self, connection) -> int | None:
        if not isinstance(getattr(connection, "credits", None), int):
            return None
        return connection.credits - self.reserved_credits.get(connection.id, 0)

    def can_reserve(self, connection, credit_cost: int = 0) -> bool:
        if self.connection_load(connection) >= connection.max_slots:
            return False
        if credit_cost:
            available = self.available_credits(connection)
            if available is None or available < credit_cost:
                return False
        return True

    def reserve_connection(self, connection, credit_cost: int = 0) -> bool:
        if not self.can_reserve(connection, credit_cost):
            return False
        self.active_jobs[connection.id] = self.active_jobs.get(connection.id, 0) + 1
        if credit_cost:
            self.reserved_credits[connection.id] = (
                self.reserved_credits.get(connection.id, 0) + credit_cost
            )
        return True

    def release_connection(self, connection_id: str, credit_cost: int = 0) -> None:
        remaining = self.active_jobs.get(connection_id, 0) - 1
        if remaining > 0:
            self.active_jobs[connection_id] = remaining
        else:
            self.active_jobs.pop(connection_id, None)
        if credit_cost:
            credits_remaining = self.reserved_credits.get(connection_id, 0) - credit_cost
            if credits_remaining > 0:
                self.reserved_credits[connection_id] = credits_remaining
            else:
                self.reserved_credits.pop(connection_id, None)

    def select_connection(self, available):
        return min(
            available,
            key=lambda item: (
                item.connected_at if hasattr(item, "connected_at") else 0,
                item.installation_id,
            ),
        )

    def project_lock(self, installation_id: str) -> asyncio.Lock:
        return self.project_locks.setdefault(installation_id, asyncio.Lock())

    def media_lock(self, account_key: str, project_id: str, digest: str) -> asyncio.Lock:
        key = (account_key, project_id, digest)
        lock = self.media_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.media_locks[key] = lock
        return lock


def build_runtime(settings) -> Runtime:
    bridge = FlowBridge(
        flow_api_key=settings.flow_api_key,
        slot_capacity=settings.account_slot_capacity,
        cooldown_seconds=settings.account_rate_limit_cooldown_seconds,
    )
    projects = ProjectStore(settings.project_store_path)
    projects.prune()
    return Runtime(
        settings,
        bridge,
        ExtensionManager(bridge),
        projects,
    )
