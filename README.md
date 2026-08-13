# FlowProviderAPI

Stateless Google Flow execution gateway for FlowCanvas through a Chrome MV3 connector. FlowCanvas owns durable jobs, idempotency, authorization, media metadata and object storage; this service owns only live provider execution.

## Production contract

FlowCanvas calls `POST /v2/gateway/generations` with:

- `Authorization: Bearer <FLOW_PROVIDER_BOOTSTRAP_API_KEY>`
- a stable `Idempotency-Key` for the logical FlowCanvas submission
- `storage_mode: "caller_owned"`
- signed HTTPS input and output URLs on explicitly allowlisted hosts

The endpoint supports `image`, image-to-`video`, and `omni`. It creates a temporary Google Flow project, uploads caller references when needed, executes or polls the generation, uploads every output directly to FlowCanvas storage, and returns output index, MIME type, byte size and SHA-256 checksum. It never returns Google URLs, signed caller URLs, or Provider media IDs.

The gateway contains no PostgreSQL, Alembic migrations, worker, R2/local asset storage, media API or admin dashboard. The legacy V1 runtime has been removed.

`Idempotency-Key` produces a deterministic gateway task identifier. FlowCanvas must own the durable idempotency lock, payload-conflict check and uncertain-execution recovery because this gateway intentionally stores no request state.

## Configuration

Required production values:

```env
FLOW_PROVIDER_ENV=production
FLOW_PROVIDER_PUBLIC_BASE_URL=https://provider.example.com
FLOW_PROVIDER_BOOTSTRAP_API_KEY=fpa_prod_<secret>
FLOW_PROVIDER_CALLER_OWNED_ALLOWED_HOSTS=storage.flowcanvas.example
```

`FLOW_PROVIDER_CALLER_OWNED_ALLOWED_HOSTS` is a comma-separated list of exact hostnames. It is an SSRF boundary, not storage credentials. Do not include schemes, paths, wildcards, or general-purpose hosts.

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

See [deployment](docs/deployment.md) and [caller-owned storage V2](docs/caller-owned-storage-v2.md).

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

The suite covers the stateless boot boundary and image/video/Omni gateway contracts. Production acceptance must also exercise real generations with signed FlowCanvas URLs and a signed-in Google Flow profile.
