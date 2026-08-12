from __future__ import annotations

from dataclasses import dataclass

from app.assets.service import AssetService
from app.assets.storage import build_storage
from app.auth.rate_limit import RateLimiter
from app.db.session import build_engine, build_session_factory
from app.extension.manager import ExtensionManager
from app.jobs.scheduler import GlobalScheduler
from app.jobs.worker import JobWorker
from app.providers.google_flow.browser_bridge import FlowBridge
from app.providers.google_flow.provider import GoogleFlowProvider
from app.providers.registry import ProviderRegistry


@dataclass
class Runtime:
    settings: object
    engine: object
    session_factory: object
    storage: object
    assets: AssetService
    bridge: FlowBridge
    extension_manager: ExtensionManager
    providers: ProviderRegistry
    scheduler: GlobalScheduler
    rate_limiter: RateLimiter
    worker: JobWorker | None = None


def build_runtime(settings, *, extra_providers: list | None = None) -> Runtime:
    engine=build_engine(settings.database_url);session_factory=build_session_factory(engine)
    storage=build_storage(settings);assets=AssetService(storage,settings)
    bridge=FlowBridge(flow_api_key=settings.flow_api_key,slot_capacity=settings.account_slot_capacity,cooldown_seconds=settings.account_rate_limit_cooldown_seconds)
    extension_manager=ExtensionManager(bridge)
    providers=ProviderRegistry();providers.register(GoogleFlowProvider(bridge,assets))
    for provider in extra_providers or []: providers.register(provider)
    runtime=Runtime(settings,engine,session_factory,storage,assets,bridge,extension_manager,providers,GlobalScheduler(bridge),RateLimiter())
    runtime.worker=JobWorker(runtime);return runtime
