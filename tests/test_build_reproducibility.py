from __future__ import annotations

import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _dependency_name(spec: str) -> str:
    return re.split(r"[\[<>=!~; ]", spec, maxsplit=1)[0].strip().lower()


def _constraint_names() -> set[str]:
    names = set()
    for raw in (ROOT / "constraints.lock").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        assert "==" in line, f"constraint is not exact: {line}"
        names.add(line.split("==", 1)[0].strip().lower())
    return names


def test_all_direct_python_dependencies_are_exactly_constrained():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    direct = list(pyproject["project"]["dependencies"])
    for values in pyproject["project"].get("optional-dependencies", {}).values():
        direct.extend(values)

    constraints = _constraint_names()
    missing = sorted({_dependency_name(spec) for spec in direct} - constraints)
    assert missing == []


def test_production_image_uses_pinned_base_and_constraints():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    first_line = dockerfile.splitlines()[0]
    assert re.fullmatch(r"FROM python:3\.13-slim@sha256:[0-9a-f]{64}", first_line)
    assert "COPY pyproject.toml README.md constraints.lock ./" in dockerfile
    assert "pip install --no-cache-dir --constraint constraints.lock ." in dockerfile
    assert "pip check" in dockerfile


def test_ci_uses_same_constraints_and_builds_production_image():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "pip install --constraint constraints.lock -e '.[dev]'" in workflow
    assert "docker build -t flowprovider-ci ." in workflow
