# FlowProviderAPI

Google Flow API facade backed by a signed-in Chrome MV3 connector. The service owns only live connection selection and request/response forwarding.

## Production contract

Clients call fixed business endpoints for projects, image upload, image generation and video generation. The backend selects an available extension, lets the browser add Google authentication/captcha, and returns the upstream HTTP status and body unchanged.

The facade does not create jobs, poll automatically, download media, manage storage, or create a Provider response model. It contains no database, worker, asset service or admin dashboard.

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
