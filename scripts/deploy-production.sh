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
import os
import urllib.error
import urllib.request

base_url = "http://127.0.0.1:8000"
with urllib.request.urlopen(base_url + "/openapi.json", timeout=5) as response:
    schema = json.load(response)
paths = schema["paths"]
required = {"/v1/media", "/v1/images/generations", "/v1/videos/generations", "/v1/jobs/status"}
if not required.issubset(set(paths)):
    raise SystemExit(f"missing required public API endpoints: {sorted(required - set(paths))}")
security = schema.get("components", {}).get("securitySchemes", {})
if "BearerAuth" not in security:
    raise SystemExit("OpenAPI is missing the BearerAuth security scheme")

body = json.dumps({"job_ids": ["deploy_smoke_missing"]}).encode("utf-8")
unauthenticated = urllib.request.Request(
    base_url + "/v1/jobs/status",
    data=body,
    method="POST",
    headers={"Content-Type": "application/json"},
)
try:
    urllib.request.urlopen(unauthenticated, timeout=5)
except urllib.error.HTTPError as exc:
    if exc.code != 401:
        raise
    payload = json.loads(exc.read().decode("utf-8"))
    if payload.get("error", {}).get("code") != "INVALID_API_KEY":
        raise SystemExit("unauthenticated /v1 request did not return INVALID_API_KEY")
else:
    raise SystemExit("unauthenticated /v1 request was accepted")

api_key = os.environ.get("FLOW_PROVIDER_BOOTSTRAP_API_KEY", "").strip()
if not api_key:
    raise SystemExit("FLOW_PROVIDER_BOOTSTRAP_API_KEY is missing inside the API container")
authenticated = urllib.request.Request(
    base_url + "/v1/jobs/status",
    data=body,
    method="POST",
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    },
)
with urllib.request.urlopen(authenticated, timeout=5) as response:
    payload = json.load(response)
if payload.get("jobs", [{}])[0].get("error", {}).get("code") != "JOB_NOT_FOUND":
    raise SystemExit("authenticated /v1 job-status smoke response is unexpected")

try:
    urllib.request.urlopen(base_url + "/admin", timeout=5)
except urllib.error.HTTPError as exc:
    if exc.code != 404:
        raise
else:
    raise SystemExit("legacy admin surface is enabled")
print("Google Flow facade API surface and production auth contract are ready.")
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
