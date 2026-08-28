# Production deployment

Production runs FlowProviderAPI and `cloudflared`. It uses a named Docker volume for the SQLite project/media/operation mapping store. PostgreSQL, asset storage, workers, Alembic and the admin dashboard remain unnecessary.

## Configure

```bash
cp .env.production.example .env.production
chmod 600 .env.production
```

Set an HTTPS public URL, a strong `FLOW_PROVIDER_EXTENSION_API_KEY`, and the Cloudflare Tunnel token. Set `FLOW_PROVIDER_ALLOW_SIMULATION_MODE=false`. The `/v1/*` business API is intentionally public, while the private Chrome connector still requires its own key. Copy `extension/config.local.example.js` to the Git-ignored `extension/config.local.js` and put the extension key there before packaging or loading the private connector. Keep the environment file out of Git.

The production image copies only the installed Python application. `.dockerignore` excludes environment files, local databases, Git metadata, virtual environments, test artifacts, and local API-key files from the Docker build context.

The Cloudflare published application route should forward the Provider hostname to `http://api:8000`. Do not place an interactive challenge on the `/v1/*` Flow endpoints or `/api/extensions/ws`. Apply normal WAF/DDoS controls and an IP-based rate limit to extension WebSocket handshakes.

## Deploy

```bash
bash scripts/deploy-production.sh
```

The helper validates Compose interpolation, builds the API, starts API and tunnel, waits for health, then verifies the fixed Google Flow business endpoint surface and that `/admin` is absent.

## Verify

```bash
curl -fsS https://provider.example.com/health/live
curl -fsS https://provider.example.com/health/ready
```

Install `extension/` in a Chrome profile signed in to Google Flow and configure it to connect to the Provider HTTPS hostname. Final acceptance must send a real request through a fixed Flow endpoint and verify that its upstream HTTP status and body reach the client unchanged.

Back up the `provider_data` Docker volume before destructive host migration. The SQLite database contains only account/project routes, image hashes, media IDs and video operation routes; it contains no Google credentials or image bytes.
