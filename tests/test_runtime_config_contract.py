from __future__ import annotations

from pathlib import Path

from app.config import Settings


ROOT = Path(__file__).resolve().parents[1]


def _env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_dispatch_lease_default_matches_paid_request_safety_window():
    settings = Settings()
    assert settings.worker_dispatch_lease_seconds == 900


def test_obsolete_worker_running_timeout_setting_is_not_exposed():
    settings = Settings()
    assert not hasattr(settings, "worker_running_timeout_seconds")
    for name in (".env.example", ".env.production.example"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "FLOW_PROVIDER_WORKER_RUNNING_TIMEOUT_SECONDS" not in text


def test_env_examples_document_the_runtime_job_timeout_settings():
    settings = Settings()
    expected = {
        "FLOW_PROVIDER_WORKER_DISPATCH_LEASE_SECONDS": str(settings.worker_dispatch_lease_seconds),
        "FLOW_PROVIDER_JOB_IMAGE_TIMEOUT_SECONDS": str(settings.job_image_timeout_seconds),
        "FLOW_PROVIDER_JOB_VIDEO_QUEUE_TIMEOUT_SECONDS": str(settings.job_video_queue_timeout_seconds),
        "FLOW_PROVIDER_JOB_VIDEO_RUNNING_TIMEOUT_SECONDS": str(settings.job_video_running_timeout_seconds),
    }
    for name in (".env.example", ".env.production.example"):
        values = _env_values(ROOT / name)
        for key, value in expected.items():
            assert values.get(key) == value, f"{name}: {key} drifted from Settings"
