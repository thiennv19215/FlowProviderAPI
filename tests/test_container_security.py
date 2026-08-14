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
