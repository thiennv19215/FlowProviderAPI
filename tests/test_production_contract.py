import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def production_app(tmp_path):
    return create_app(Settings(
        env="production",
        bootstrap_api_key="fpa_prod_business_secret",
        extension_api_key="fpe_prod_connector_secret",
        public_base_url="https://provider.test",
        project_store_path=str(tmp_path / "projects.db"),
        asset_store_path=str(tmp_path / "assets"),
        worker_enabled=False,
    ))


def test_production_requires_business_api_key(tmp_path):
    common = {
        "env": "production",
        "extension_api_key": "fpe_prod_connector_secret",
        "public_base_url": "https://provider.test",
        "project_store_path": str(tmp_path / "projects.db"),
        "asset_store_path": str(tmp_path / "assets"),
    }
    with pytest.raises(ValueError, match="business API key"):
        Settings(**common)


def test_production_rejects_shared_business_and_extension_keys(tmp_path):
    with pytest.raises(ValueError, match="must be different"):
        Settings(
            env="production",
            bootstrap_api_key="prod_shared_secret",
            extension_api_key="prod_shared_secret",
            public_base_url="https://provider.test",
            project_store_path=str(tmp_path / "projects.db"),
            asset_store_path=str(tmp_path / "assets"),
        )


def test_production_business_endpoints_require_bearer_api_key(tmp_path):
    application = production_app(tmp_path)
    body = {"job_ids": ["job_missing"]}
    with TestClient(application) as client:
        missing = client.post("/v1/jobs/status", json=body)
        wrong = client.post(
            "/v1/jobs/status",
            headers={"Authorization": "Bearer wrong"},
            json=body,
        )
        accepted = client.post(
            "/v1/jobs/status",
            headers={"Authorization": "Bearer fpa_prod_business_secret"},
            json=body,
        )

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "INVALID_API_KEY"
    assert missing.headers["www-authenticate"] == "Bearer"
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "INVALID_API_KEY"
    assert accepted.status_code == 200
    assert accepted.json()["jobs"][0]["error"]["code"] == "JOB_NOT_FOUND"


def test_production_readiness_is_safe_and_requires_provider(tmp_path):
    application = production_app(tmp_path)
    with TestClient(application) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")
        extension_health = client.get("/api/health")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 503
    assert ready.json()["status"] == "waiting_for_provider"
    assert "accounts" not in ready.json()
    assert extension_health.status_code == 200
    assert extension_health.json()["ok"] is False
    assert "accounts" not in extension_health.json()


def test_openapi_marks_business_routes_as_bearer_authenticated(tmp_path):
    schema = production_app(tmp_path).openapi()
    assert schema["components"]["securitySchemes"]["BearerAuth"]["scheme"] == "bearer"
    assert schema["paths"]["/v1/jobs/status"]["post"]["security"] == [
        {"BearerAuth": []}
    ]
