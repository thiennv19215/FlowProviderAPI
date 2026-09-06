# FlowProviderAPI

Google Flow API and orchestration service backed by signed-in Chrome MV3 connectors. The service exposes a stable server-to-server API, selects an available Google Flow account, manages project/media routing, persists durable jobs in SQLite, and forwards browser-authenticated generation requests through the private extension connector.

## Production contract

Production business endpoints under `/v1/*` require:

```http
Authorization: Bearer <FLOW_PROVIDER_BOOTSTRAP_API_KEY>
Content-Type: application/json
```

`FLOW_PROVIDER_BOOTSTRAP_API_KEY` is the credential for trusted backend callers. `FLOW_PROVIDER_EXTENSION_API_KEY` is a separate credential used only between this Provider and Chrome extensions. Production refuses to start if either is missing, uses a development placeholder, or both keys are identical.

Clients use fixed v1 endpoints for media upload, image generation, video generation, job status, and Character workflows. Image and video generation return durable Provider jobs; clients read their state from SQLite through `POST /v1/jobs/status`.

Image generation can run in compatibility mode with an explicit `project_id`, or managed mode without one. The API resolves references, writes an image job, and returns `202`; the worker calls Flow once and stores the terminal result without upstream polling. Video jobs are dispatched once and then polled by the worker. In managed mode the Provider chooses a ready extension, reuses that account's newest project or creates `FlowProvider` when none exists, and caches project and media routes in SQLite.

Scheduling selects a ready extension by capacity/load and available credits. Video jobs reserve their estimated credit cost before dispatch so concurrent requests cannot reuse the same visible balance. After every paid attempt, including an uncertain timeout, paid routing remains blocked until credit refresh succeeds. Image operations remain eligible.

For multi-account-safe video generation, callers pass reference image bytes through `input_images`. The Provider hashes and persists the bytes, deduplicates uploads per account/project, and can rehydrate them when routing changes. Google project/media ownership is never silently moved to an unrelated account.

Provider jobs expose a normalized lifecycle:

```text
queued -> running -> complete | failed
```

Generation endpoints support `Idempotency-Key`. A repeated logical request with the same key and payload returns the existing job; reusing a key with a different payload returns a conflict. Paid video callers should always use idempotency keys.

Current default job timeouts are image 120s, video queue 180s, and active video render/poll 600s. Status responses tell clients when to poll again.

## Character workflows

Character workflows are separate from generic generation endpoints. Upload source images through `POST /v1/media`, register them with `POST /v1/characters`, then call `/v1/characters/{id}/images/generations` or `/v1/characters/{id}/videos/generations`.

Character reference bytes are retained in the configured asset store so they survive Google signed-URL expiry and account changes. Character output never replaces its stored references.

## Required production configuration

```env
FLOW_PROVIDER_ENV=production
FLOW_PROVIDER_PUBLIC_BASE_URL=https://provider.example.com
FLOW_PROVIDER_BOOTSTRAP_API_KEY=fpa_prod_<business-secret>
FLOW_PROVIDER_EXTENSION_API_KEY=fpe_prod_<different-connector-secret>
FLOW_PROVIDER_WORKER_ENABLED=true
```

Keep both keys server-side. Never expose either credential in a frontend application. The business key belongs only to trusted services calling `/v1/*`; the connector key belongs only to the private Chrome extension.

Copy `extension/config.local.example.js` to the Git-ignored `extension/config.local.js`, then set the extension connector key before packaging or loading the private connector.

## Health

- `GET /health/live` — process liveness only; safe for container healthchecks.
- `GET /health/ready` — serving readiness; returns `200` only when the store and at least one Google Flow connector are ready, otherwise `503`.
- `GET /api/health` — safe aggregate connector readiness for extension tooling.
- `WS /api/extensions/ws` — private Chrome connector gateway; authenticated separately in production.

Public health responses do not expose Google account emails, detailed credit balances, or connector error details.

## Run locally

```bash
cp .env.example .env
pip install -e '.[dev]'
uvicorn app.main:app --reload
```

Load `extension/` as an unpacked Chrome extension in a Chrome profile signed in to Google Flow. Open `http://localhost:8000/docs` for the active OpenAPI document.

## Deploy

The production Compose stack contains FlowProviderAPI and Cloudflare Tunnel:

```bash
cp .env.production.example .env.production
bash scripts/deploy-production.sh
```

The container healthcheck uses `/health/live` so Cloudflare Tunnel can start even before a browser connector is online. External callers should use `/health/ready` to decide whether generation work can be served.

See `docs/deployment.md` for deployment details and `docs/integration-guide.vi.md` for the current server-to-server API contract.

## Tests

```bash
python -m pytest -q
```

CI also checks extension JavaScript, manifest syntax, and production Compose configuration. The `dev` integration branch and all pull requests are CI-gated. Production acceptance must additionally exercise a real generation through a signed-in Google Flow profile before promotion to `main`.
