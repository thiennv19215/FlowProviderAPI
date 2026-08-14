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

    def connection_load(self, connection) -> int:
        return max(
            self.active_jobs.get(connection.id, 0),
            self.bridge.pending_count(connection.id),
        )

    def reserve_connection(self, connection) -> bool:
        if self.connection_load(connection) >= connection.max_slots:
            return False
        self.active_jobs[connection.id] = self.active_jobs.get(connection.id, 0) + 1
        return True

    def release_connection(self, connection_id: str) -> None:
        remaining = self.active_jobs.get(connection_id, 0) - 1
        if remaining > 0:
            self.active_jobs[connection_id] = remaining
        else:
            self.active_jobs.pop(connection_id, None)

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
