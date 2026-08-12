# FlowProviderAPI

Shared, developer-facing Google Flow media API through a Chrome MV3 connector. The public generation contract stays deliberately small while provider orchestration remains internal.

## What it owns

- `/v1` image, video and Omni generation contracts
- durable PostgreSQL generation jobs with leases
- provider account scheduling/capacity and cooldowns
- Google Flow project/media mapping
- Provider-owned assets in local storage or Cloudflare R2
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

Open `http://localhost:8000/docs` for Swagger. Load `extension/` as an unpacked Chrome extension and point its popup at the Provider URL.

For a live Google Flow test, open Google Flow in the same Chrome profile. The extension discovers the Google Flow API key from requests to Google's Flow API and supplies it to the backend for the lifetime of the connection. `FLOW_PROVIDER_FLOW_API_KEY` remains available as an optional server-side fallback. Local/test mode may leave the extension gateway token unset. Production requires `FLOW_PROVIDER_EXTENSION_GATEWAY_TOKEN` with at least 32 characters. In the extension popup, configure the Provider server as `https://provider.example.com` and put the same secret in the separate **Gateway token** field. The connector sends that secret only in the WebSocket subprotocol during the `/api/extensions/ws` handshake; it is not embedded in the request URL. Existing saved `/ext/<token>` settings are migrated automatically once to the separate token storage.

## Primary endpoints

- `POST /v1/images/generations`
- `POST /v1/videos/image-to-video`
- `POST /v1/videos/omni-generations`
- `GET /v1/jobs/{job_id}`
- `POST /v1/jobs/{job_id}/cancel`
- `POST /v1/assets/uploads`
- `GET /v1/assets/{asset_id}`
- `GET /v1/accounts`
- `GET /v1/health`

Operational probes `/health/live` and `/health/ready`, plus the extension-only `/api/health`, remain available but are intentionally hidden from the public OpenAPI document.

## Runtime hardening

The current V1 preserves provider account identity across extension reconnects, invalidates stale signed-out accounts, reserves estimated credits from active jobs, uses duration-aware Omni credit costs, distinguishes terminal provider failures from transient polling failures, and bounds consecutive polling failures so jobs cannot remain `running` forever on a persistent provider error.

Provider output downloads are host-allowlisted, size-bounded, and streamed through temporary files into local/R2 storage instead of buffering whole videos in process memory. Presigned upload completion validates declared size/content type and deletes rejected objects. Google Flow reference uploads have a separate in-memory hard limit before base64 encoding. Readiness checks both the database and configured storage backend.

`POST /v1/jobs/{id}/cancel` is currently cooperative at the Provider job layer. The current Google Flow integration does not contain a verified upstream cancel-generation primitive, so the service deliberately does not invent or call an unverified Google endpoint.

## Mock extension E2E

The test suite includes a stateful mock Chrome extension in `tests/mock_extension.py`. It implements the browser RPC boundary used by the real MV3 connector: bearer capture, Flow tab handling, reCAPTCHA, `SW_FETCH`, page fetch, account credits/tier, project creation, media upload, image generation, standard video, Omni and async video polling.

The tests therefore execute the real backend stack below the browser boundary:

```text
REST API -> GenerationJob -> Worker -> Global Scheduler
         -> GoogleFlowProvider -> FlowSDK -> FlowBridge
         -> Mock Extension RPC -> Provider output -> Asset storage
```

Run the complete suite with:

```bash
python -m pytest -q
```

The mock extension is intentionally not a substitute for final live acceptance. Before production cutover, deploy the Provider, load `extension/` in a Chrome profile signed into Google Flow, and run real image/video/Omni generations.

See [`docs/quickstart.md`](docs/quickstart.md) and [`docs/architecture.md`](docs/architecture.md).
