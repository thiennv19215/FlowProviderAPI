from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_container_build_never_copies_the_repository_or_secrets():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "COPY . ." not in dockerfile
    assert ".env.*" in dockerignore
    assert ".flowprovider-api-keys.local" in dockerignore
    assert ".git" in dockerignore
    assert ".data" in dockerignore
    assert ".venv" in dockerignore


def test_production_api_runs_as_non_root_with_no_new_privileges():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")

    assert "USER 10001:10001" in dockerfile
    assert "chown -R 10001:10001 /data" in dockerfile
    assert 'user: "10001:10001"' in compose
    assert "no-new-privileges:true" in compose


def test_deploy_migrates_existing_volume_before_starting_non_root_api():
    deploy = (ROOT / "scripts/deploy-production.sh").read_text(encoding="utf-8")

    assert "prepare_data_volume()" in deploy
    assert "chown -R 10001:10001 /data" in deploy
    assert 'if os.geteuid() == 0:' in deploy
    assert '/data/.flowprovider-write-probe' in deploy

    build_pos = deploy.index('"${compose[@]}" build --pull api')
    migrate_pos = deploy.index("prepare_data_volume", build_pos)
    start_pos = deploy.index('"${compose[@]}" up -d --remove-orphans', migrate_pos)
    assert build_pos < migrate_pos < start_pos


def test_ci_verifies_non_root_named_volume_access():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "Verify non-root runtime and data-volume access" in workflow
    assert 'test "$(id -u)" = 10001' in workflow
    assert 'test "$(id -g)" = 10001' in workflow
    assert 'touch /data/.write-test' in workflow
