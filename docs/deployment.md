# Stateless production deployment

Production runs only FlowProviderAPI and `cloudflared`. PostgreSQL, Provider R2 credentials, asset volumes, workers, Alembic startup migrations and the admin dashboard are intentionally absent. FlowCanvas owns all durable state and storage.

## Configure

```bash
cp .env.production.example .env.production
chmod 600 .env.production
```

Set an HTTPS public URL, a strong `FLOW_PROVIDER_BOOTSTRAP_API_KEY`, the exact FlowCanvas storage hosts in `FLOW_PROVIDER_CALLER_OWNED_ALLOWED_HOSTS`, and the Cloudflare Tunnel token. Keep the environment file out of Git.

The Cloudflare published application route should forward the Provider hostname to `http://api:8000`. Do not place an interactive challenge on the three generation endpoints or `/api/extensions/ws`. Apply normal WAF/DDoS controls and an IP-based rate limit to extension WebSocket handshakes.

## Deploy

```bash
bash scripts/deploy-production.sh
```

The helper validates Compose interpolation, builds the API, starts API and tunnel, waits for health, then verifies that only the three specialized generation endpoints are public business endpoints and `/admin` is absent.

## Verify

```bash
curl -fsS https://provider.example.com/health/live
curl -fsS https://provider.example.com/health/ready
```

Install `extension/` in a Chrome profile signed in to Google Flow and configure it to connect to the Provider HTTPS hostname. Final acceptance must execute a real image, image-to-video and Omni request using short-lived FlowCanvas URLs and verify the resulting objects and checksums in FlowCanvas storage.

The gateway has no database backup procedure. Backups, retention and disaster recovery belong to FlowCanvas and its object storage.
