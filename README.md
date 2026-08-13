# FlowProviderAPI

Shared, developer-facing Google Flow media API through a Chrome MV3 connector. The public generation contract stays deliberately small while provider orchestration remains internal.

## What it owns

- unified `/v1/generations` orchestration plus native image, video and Omni generation contracts
- durable PostgreSQL generation jobs with leases
- provider account scheduling/capacity and cooldowns
- Google Flow project/media mapping
- compact 15-digit string media references with authenticated Provider delivery URLs
- durable, content-deduplicated reference-upload storage in a local Docker volume
- durable idempotency for unified application-orchestrator submissions
- direct Chrome extension WebSocket protocol v7
- API keys, client concurrency limits, rate-limit headers, structured errors

It **does not** own FlowCanvas Boards/Nodes/Pipelines or other application business logic.

## Local start

```bash
cp .env.example .env
pip install -e '.[dev]'
alembic upgrade head
uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for Swagger. Load `extension/` as an unpacked Chrome extension. For a live Google Flow test, open Google Flow in the same Chrome profile.

## Production deployment

The production VPS stack is defined in `compose.production.yaml`: PostgreSQL, FlowProviderAPI, and a remotely-managed Cloudflare Tunnel run together without publishing API or database ports on the VPS.

```bash
cp .env.production.example .env.production
bash scripts/deploy-production.sh
```

See [`docs/deployment.md`](docs/deployment.md) for the complete production procedure.

## Primary endpoints

Application backends should prefer the unified contract:

- `POST /v1/generations`
- `GET /v1/status/{task_id}`
- `GET /v1/status`
- `POST /v1/status/{task_id}/cancel`
- `POST /v1/media`
- `GET /v1/media/{media_id}`

For `POST /v1/generations`, send one stable `Idempotency-Key` per logical remote submission. A retry of the same normalized request with the same API client and key returns the same durable task; reusing the key for a different submission returns `409 IDEMPOTENCY_KEY_CONFLICT`. Persist the returned `task_id` immediately and poll `/v1/status/{task_id}` while work is queued or running.

Native compatibility generation endpoints remain available:

- `POST /v1/images/generations`
- `POST /v1/videos/image-to-video`
- `POST /v1/videos/omni-generations`

Additional operational/client endpoints include `GET /v1/accounts` and `GET /v1/health`.

Backend services should start with the [backend integration reference](docs/backend-integration.md), which includes upload, image, image-to-video, Omni, polling, thumbnail, and authenticated media-delivery examples. Teams updating an existing integration should use the [endpoint update and migration guide](docs/endpoint-update.md). UI implementations should use the [frontend integration guide](docs/frontend-integration.md).

Operational probes `/health/live` and `/health/ready`, plus the extension-only `/api/health`, remain available but are intentionally hidden from the public OpenAPI document.

## Runtime hardening

The current V1 preserves provider account identity across extension reconnects, invalidates stale signed-out accounts, reserves estimated credits from active jobs, uses duration-aware Omni credit costs, distinguishes terminal provider failures from transient polling failures, and bounds consecutive polling failures so jobs cannot remain `running` forever on a persistent provider error.

Provider output URLs are host-allowlisted, downloaded and copied into Provider-owned storage before they enter the public result. User-supplied references are validated, SHA-256 content-deduplicated per API client and stored by `POST /v1/media`. Public media URLs keep the Provider API as the authorization boundary: R2-backed objects are delivered through short-lived presigned redirects, while legacy local objects are streamed by the API during migration.

The public extension WebSocket accepts unauthenticated connector registrations by design. Keep Cloudflare WAF/DDoS protections enabled and apply an IP-based rate limit to WebSocket handshakes for `/api/extensions/ws` without an interactive challenge.

Cancellation is cooperative at the Provider job layer. The Google Flow adapter does not claim an upstream generation has been canceled unless a verified upstream cancellation primitive exists.

## Tests

Run the complete suite with:

```bash
python -m pytest -q
```

The mock extension tests execute the real backend stack below the browser boundary. Final production acceptance should still include real image/video/Omni generations against a signed-in Google Flow profile.
