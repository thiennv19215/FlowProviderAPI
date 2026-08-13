# Stateless production deployment

Production runs only FlowProviderAPI and `cloudflared`. PostgreSQL, asset volumes, workers, Alembic startup migrations and the admin dashboard are intentionally absent.

## Configure

```bash
cp .env.production.example .env.production
chmod 600 .env.production
```

Set an HTTPS public URL, a strong `FLOW_PROVIDER_BOOTSTRAP_API_KEY`, and the Cloudflare Tunnel token. Keep the environment file out of Git.

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

The facade has no database backup procedure because it owns no durable state.
