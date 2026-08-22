# FlowProviderAPI

Google Flow API and orchestration service backed by signed-in Chrome MV3 connectors. The service selects an available account, remembers its managed Google Flow project, and forwards browser-authenticated generation requests.

## Production contract

Clients call fixed business endpoints for projects, image upload, image generation and video generation. The backend selects an available extension, lets the browser add Google authentication/captcha, and returns the upstream HTTP status and body unchanged.

Image generation can run in either compatibility mode with an explicit `project_id`, or managed mode without one. In managed mode the Provider chooses a ready extension, recovers or creates that installation/account's `FlowProvider` project once, and caches project and uploaded-media IDs in SQLite. Repeated inline images are matched by SHA-256 within the same installation, Google account, and project and reuse their media ID directly. A rare stale-media `404` invalidates the cache and triggers one upload retry. Google credentials and image bytes remain browser-owned/request-scoped.

Scheduling reserves up to three complete HTTP jobs on one ready extension before moving to the next. Each video job also reserves at least 20 credits, or its higher known Omni cost, before it starts, so concurrent requests cannot reuse the same visible balance. After every paid attempt, including an uncertain timeout, paid routing remains blocked until the managed credit refresh succeeds. Image operations remain eligible.

Video operation routes are stored by account/project. Status requests without a routing scope are split by owning account and merged, so polling cannot move to a different Google account.

## Configuration

Required production values:

```env
FLOW_PROVIDER_ENV=production
FLOW_PROVIDER_PUBLIC_BASE_URL=https://provider.example.com
FLOW_PROVIDER_BOOTSTRAP_API_KEY=fpa_prod_<secret>
```

## Run locally

```bash
cp .env.example .env
pip install -e '.[dev]'
uvicorn app.main:app --reload
```

Load `extension/` as an unpacked Chrome extension in a Chrome profile signed in to Google Flow. Open `http://localhost:8000/docs` for the active gateway OpenAPI document.

## Deploy

The production Compose stack contains only FlowProviderAPI and Cloudflare Tunnel:

```bash
cp .env.production.example .env.production
bash scripts/deploy-production.sh
```

See [deployment](docs/deployment.md).

Integration documentation: [Vietnamese API integration guide](docs/integration-guide.vi.md).

AI agents can use the included MCP adapter over local `stdio`. See [Vietnamese MCP agent guide](docs/mcp-agent.vi.md).

## Operational endpoints

- `GET /health/live`
- `GET /health/ready`
- `GET /api/health`
- `WS /api/extensions/ws`

The extension WebSocket is unauthenticated by design. Keep WAF/DDoS protection and an IP-based handshake rate limit without an interactive challenge.

## Tests

```bash
python -m pytest -q
```

The suite covers authentication, connection selection, fixed Flow operations and transparent upstream responses. Production acceptance must also exercise a real request through a signed-in Google Flow profile.
