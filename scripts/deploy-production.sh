#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env.production}"
COMPOSE_FILE="${COMPOSE_FILE:-${ROOT_DIR}/compose.production.yaml}"

cd "${ROOT_DIR}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose plugin is required" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "missing ${ENV_FILE}; copy .env.production.example and fill the required values" >&2
  exit 1
fi

compose=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}")

verify_gateway_surface() {
  "${compose[@]}" exec -T api python - <<'PY'
import json
import urllib.request
import urllib.error

base_url = "http://127.0.0.1:8000"
with urllib.request.urlopen(base_url + "/openapi.json", timeout=5) as response:
    paths = json.load(response)["paths"]
if set(paths) != {"/v1/generations"}:
    raise SystemExit(f"unexpected public API surface: {sorted(paths)}")
try:
    urllib.request.urlopen(base_url + "/admin", timeout=5)
except urllib.error.HTTPError as exc:
    if exc.code != 404:
        raise
else:
    raise SystemExit("legacy admin surface is enabled")
print("Stateless gateway API surface is ready.")
PY
}

# Validate interpolation and required variables before touching running services.
"${compose[@]}" config >/dev/null

# Refresh third-party images, rebuild the API, and reconcile the stateless stack.
"${compose[@]}" pull cloudflared
"${compose[@]}" build --pull api
"${compose[@]}" up -d --remove-orphans

"${compose[@]}" ps

# Fail the deployment command if the API did not become healthy.
container_id="$("${compose[@]}" ps -q api)"
if [[ -z "${container_id}" ]]; then
  echo "api container is not running" >&2
  exit 1
fi

for _ in $(seq 1 30); do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}")"
  if [[ "${status}" == "healthy" ]]; then
    echo "FlowProviderAPI is healthy."
    if ! verify_gateway_surface; then
      "${compose[@]}" logs --tail=200 api >&2 || true
      echo "FlowProviderAPI is healthy, but the gateway surface verification failed." >&2
      exit 1
    fi
    exit 0
  fi
  if [[ "${status}" == "unhealthy" || "${status}" == "exited" || "${status}" == "dead" ]]; then
    "${compose[@]}" logs --tail=200 api >&2 || true
    echo "FlowProviderAPI failed health checks (${status})." >&2
    exit 1
  fi
  sleep 2
done

"${compose[@]}" logs --tail=200 api >&2 || true
echo "FlowProviderAPI did not become healthy within the deployment check window." >&2
exit 1
