# FlowProviderAPI

Google Flow API and orchestration service backed by signed-in Chrome MV3 connectors. The service selects an available account, remembers its managed Google Flow project, and forwards browser-authenticated generation requests.

## Production contract

Clients call fixed v1 business endpoints for projects, image upload, image generation and video generation. Image and video generation return durable Provider jobs; clients read their state from SQLite through `/v1/jobs/status`.

Image generation can run in either compatibility mode with an explicit `project_id`, or managed mode without one. The API resolves references, writes an image job, and returns `202`; the worker calls Flow once and stores the terminal result without upstream polling. Video jobs are dispatched once and then polled by the worker. In managed mode the Provider chooses a ready extension, reuses that account's newest project or creates `FlowProvider` when none exists, and caches project and media routes in SQLite.

Scheduling selects the least-loaded ready extension, breaking ties by connection age, and reserves up to three complete HTTP jobs per extension. Each video job also reserves at least 20 credits, or its higher known Omni cost, before it starts, so concurrent requests cannot reuse the same visible balance. After every paid attempt, including an uncertain timeout, paid routing remains blocked until the managed credit refresh succeeds. Image operations remain eligible.

Video operation routes are stored by account/project. Status requests without a routing scope are split by owning account and merged, so polling cannot move to a different Google account.

Character workflows are separate from the generic generation endpoints. Upload 1-3
source images with `POST /v1/media`, register them with `POST /v1/characters`,
then call `/v1/characters/{id}/images/generations` or
`/v1/characters/{id}/videos/generations`. Character image jobs call Flow's
`batchGenerateImages` once; Character video jobs use R2V/Omni and enter the
durable worker poller. Source bytes are retained under
`FLOW_PROVIDER_ASSET_STORE_PATH`; Character output never replaces its references.

## Configuration

Required production values:

```env
FLOW_PROVIDER_ENV=production
FLOW_PROVIDER_PUBLIC_BASE_URL=https://provider.example.com
FLOW_PROVIDER_EXTENSION_API_KEY=fpe_prod_<different-secret>
FLOW_PROVIDER_ALLOW_SIMULATION_MODE=false
```

Copy `extension/config.local.example.js` to the Git-ignored `extension/config.local.js`, then set the same extension connector key there before packaging or loading the private connector. Never put the backend business API key in the extension.

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

Integration documentation: [Vietnamese API integration guide](docs/integration-guide.vi.md), [Vietnamese Gemini Omni Flash guide](docs/gemini-omni-flash.vi.md).

AI agents can use the included MCP adapter over local `stdio`. See [Vietnamese MCP agent guide](docs/mcp-agent.vi.md), [Vietnamese practical agent playbook](docs/thuc-chien-ket-noi-agent.vi.md), and the repository-level [agent instructions](AGENTS.md).

## Operational endpoints

- `GET /health/live`
- `GET /health/ready`
- `GET /api/health`
- `WS /api/extensions/ws`

The extension WebSocket requires the connector key in production; development may omit it. Keep WAF/DDoS protection and an IP-based handshake rate limit without an interactive challenge.

## Tests

```bash
python -m pytest -q
```

The suite covers authentication, connection selection, fixed Flow operations and transparent upstream responses. Production acceptance must also exercise a real request through a signed-in Google Flow profile.
