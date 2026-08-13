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

verify_admin_dashboard() {
  "${compose[@]}" exec -T api python - <<'PY'
import os
import urllib.request

base_url = "http://127.0.0.1:8000"
admin_key = os.environ["FLOW_PROVIDER_ADMIN_API_KEY"]

for path, marker in (
    ("/admin", b"FlowProvider"),
    ("/admin-assets/app.js", b"/v1/api-clients"),
    ("/admin-assets/styles.css", b"--green"),
):
    with urllib.request.urlopen(base_url + path, timeout=5) as response:
        body = response.read()
        if response.status != 200 or marker not in body:
            raise SystemExit(f"admin dashboard verification failed for {path}")

request = urllib.request.Request(
    base_url + "/v1/api-clients",
    headers={"X-Admin-Key": admin_key},
)
with urllib.request.urlopen(request, timeout=5) as response:
    if response.status != 200:
        raise SystemExit("authenticated API-client verification failed")

print("Admin dashboard and authenticated control plane are ready.")
PY
}

# Validate interpolation and required variables before touching running services.
"${compose[@]}" config >/dev/null

# Refresh third-party images, rebuild the API from the checked-out revision,
# and reconcile the stack. The API container runs Alembic migrations on start.
"${compose[@]}" pull postgres cloudflared
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
    if ! verify_admin_dashboard; then
      "${compose[@]}" logs --tail=200 api >&2 || true
      echo "FlowProviderAPI is healthy, but the admin dashboard verification failed." >&2
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
