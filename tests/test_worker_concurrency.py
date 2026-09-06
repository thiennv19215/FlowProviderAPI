from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.runtime import ConfiguredJobWorker, build_runtime
from app.config import Settings
from app.workers.job_worker import JobWorker


@pytest.mark.asyncio
async def test_configured_worker_passes_global_concurrency_to_dispatch_loop(monkeypatch):
    observed: list[int] = []

    async def fake_process(self, max_concurrent=20):
        observed.append(max_concurrent)

    monkeypatch.setattr(JobWorker, "process_queued_jobs", fake_process)
    worker = ConfiguredJobWorker(SimpleNamespace(
        settings=SimpleNamespace(worker_concurrency=3),
    ))

    await worker.process_queued_jobs()
    await worker.process_queued_jobs(max_concurrent=10)
    await worker.process_queued_jobs(max_concurrent=2)

    assert observed == [3, 3, 2]


def test_runtime_builds_configured_worker(tmp_path):
    runtime = build_runtime(Settings(
        env="test",
        project_store_path=str(tmp_path / "projects.db"),
        asset_store_path=str(tmp_path / "assets"),
        worker_enabled=False,
        worker_concurrency=2,
    ))
    try:
        assert isinstance(runtime.worker, ConfiguredJobWorker)
        assert runtime.settings.worker_concurrency == 2
    finally:
        runtime.projects.close()


def test_worker_concurrency_is_documented_with_the_runtime_default():
    settings = Settings()
    expected = f"FLOW_PROVIDER_WORKER_CONCURRENCY={settings.worker_concurrency}"
    for name in (".env.example", ".env.production.example"):
        text = open(name, encoding="utf-8").read()
        assert expected in text
