# FlowProviderAPI

Shared, developer-facing AI media provider platform. V1 integrates Google Flow through a Chrome MV3 connector while keeping the public API provider-neutral for future OpenAI/Kling adapters.

## What it owns

- `/v1` image, video and Omni generation contracts
- durable PostgreSQL generation jobs with idempotency and leases
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

For a live Google Flow test, set `FLOW_PROVIDER_FLOW_API_KEY` in `.env`. The extension gateway intentionally has no connector authentication during the current local/test phase; add gateway authentication before exposing a production Provider endpoint publicly.

## Primary endpoints

- `POST /v1/images/generations`
- `POST /v1/videos/generations`
- `POST /v1/videos/omni-generations`
- `GET /v1/jobs/{job_id}`
- `POST /v1/jobs/{job_id}/cancel`
- `POST /v1/assets/uploads`
- `GET /v1/assets/{asset_id}`
- `GET /v1/accounts`
- `GET /v1/health`
- `GET /health/ready`

## Runtime hardening

The current V1 preserves provider account identity across extension reconnects, invalidates stale signed-out accounts, reserves estimated credits from active jobs, uses duration-aware Omni credit costs, distinguishes terminal provider failures from transient polling failures, and resumes output storage without redispatching a completed provider operation.

Provider output downloads are host-allowlisted and streamed through temporary files into local/R2 storage instead of buffering whole videos in process memory. Readiness checks both the database and configured storage backend.

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
