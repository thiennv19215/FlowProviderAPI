# FlowProviderAPI

Shared, developer-facing Google Flow media API through a Chrome MV3 connector. The public generation contract stays deliberately small while provider orchestration remains internal.

## What it owns

- unified `/v1/generations` orchestration plus native image, video and Omni generation contracts
- durable PostgreSQL generation jobs with leases
- provider account scheduling/capacity and cooldowns
- Google Flow project/media mapping
- compact 15-digit string media references with direct Google Flow output URLs
- durable, content-deduplicated reference-upload storage in a local Docker volume
- direct Chrome extension WebSocket protocol v7
- API keys, client concurrency limits, rate-limit headers, structured errors

It **does not** own FlowCanvas Boards/Nodes/Pipelines or other application business logic.

## Local start

```bash
cp .env.example .env
# for local-only smoke testing you may set SQLite:
# FLOW_PROVIDER_DATABASE_URL=sqlite:///./.data/flowprovider.db
pip install -e '.[dev]'
alembic upgrade head
uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for Swagger. Load `extension/` as an unpacked Chrome extension. An installed extension automatically connects and reconnects to its configured Provider server; the packaged default is `https://api.shopcongngheso5.io.vn`.

For a live Google Flow test, open Google Flow in the same Chrome profile. The extension discovers the Google Flow API key from requests to Google's Flow API and supplies it to the backend for the lifetime of the connection. `FLOW_PROVIDER_FLOW_API_KEY` remains available as an optional server-side fallback. The extension connector endpoint `/api/extensions/ws` is intentionally public and does not require a shared gateway token, so an installed extension can connect without manual credential setup. For a custom deployment, change only the Provider server URL in the extension popup.

## Production deployment

The production VPS stack is defined in `compose.production.yaml`: PostgreSQL, FlowProviderAPI, and a remotely-managed Cloudflare Tunnel run together without publishing API or database ports on the VPS. User-supplied reference uploads persist in a local Docker volume. Generated images and videos are returned with their direct Google Flow URLs instead of being copied into backend storage.

```bash
cp .env.production.example .env.production
# fill the required PostgreSQL, Cloudflare Tunnel and bootstrap API secrets
bash scripts/deploy-production.sh
```

Configure the Tunnel published application to route the Provider hostname to `http://api:8000`. See [`docs/deployment.md`](docs/deployment.md) for the complete VPS, asset storage, Tunnel, backup, update, and live-acceptance procedure.

## Primary endpoints

Application backends should prefer the unified contract:

- `POST /v1/generations`
- `GET /v1/tasks/{task_id}`
- `POST /v1/tasks/{task_id}/cancel`
- `POST /v1/media`
- `GET /v1/media/{media_id}`

Native compatibility endpoints remain available:

- `POST /v1/images/generations`
- `POST /v1/videos/image-to-video`
- `POST /v1/videos/omni-generations`

Additional operational/client endpoints include `GET /v1/accounts` and `GET /v1/health`.

Backend services should use the [backend integration reference](docs/backend-integration.md) or the smaller [orchestrator contract](docs/orchestrator-contract.md). UI implementations should use the [frontend integration guide](docs/frontend-integration.md).

Operational probes `/health/live` and `/health/ready`, plus the extension-only `/api/health`, remain available but are intentionally hidden from the public OpenAPI document.

## Runtime hardening

The current V1 preserves provider account identity across extension reconnects, invalidates stale signed-out accounts, reserves estimated credits from active jobs, uses duration-aware Omni credit costs, distinguishes terminal provider failures from transient polling failures, and bounds consecutive polling failures so jobs cannot remain `running` forever on a persistent provider error.

Provider output URLs are host-allowlisted before they enter the public task result. The API preserves a compact `media_id` and project-local Flow media mapping so a generated image can be reused as a reference. User-supplied files are validated, SHA-256 content-deduplicated per API client and stored by `POST /v1/media`; Google Flow reference uploads have a separate in-memory hard limit before base64 encoding. Readiness checks both the database and configured reference-upload storage backend.

The public extension WebSocket accepts unauthenticated connector registrations by design. Anyone who can reach the hostname can attempt to connect, so do not treat connector identity as trusted. Keep Cloudflare's WAF/DDoS protections enabled and apply an IP-based rate limit to WebSocket handshake requests for `/api/extensions/ws` without adding an interactive challenge that would break the extension connection.

Direct Flow URLs are controlled by the upstream provider and may expire or be revoked. A calling backend that needs a durable result should download it promptly and store its own copy.

`POST /v1/tasks/{task_id}/cancel` is currently cooperative at the Provider job layer. The current Google Flow integration does not contain a verified upstream cancel-generation primitive, so the service deliberately does not invent or call an unverified Google endpoint.

## Mock extension E2E

The test suite includes a stateful mock Chrome extension in `tests/mock_extension.py`. It implements the browser RPC boundary used by the real MV3 connector: bearer capture, Flow tab handling, reCAPTCHA, `SW_FETCH`, page fetch, account credits/tier, project creation, media upload, image generation, standard video, Omni and async video polling.

The tests therefore execute the real backend stack below the browser boundary:

```text
REST API -> GenerationJob -> Worker -> Global Scheduler
         -> GoogleFlowProvider -> FlowSDK -> FlowBridge
         -> Mock Extension RPC -> direct Provider output URL
```

Run the complete suite with:

```bash
python -m pytest -q
```

The mock extension is intentionally not a substitute for final live acceptance. Before production cutover, deploy the Provider, load `extension/` in a Chrome profile signed into Google Flow, and run real image/video/Omni generations.

See [`docs/quickstart.md`](docs/quickstart.md), [`docs/architecture.md`](docs/architecture.md), and [`docs/deployment.md`](docs/deployment.md).
