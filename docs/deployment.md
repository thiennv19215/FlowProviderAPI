# Production deployment

Production runs FlowProviderAPI and `cloudflared`. A named Docker volume stores the SQLite project/media/job mapping database and durable source assets used by Character and inline-generation recovery.

## Configure

```bash
cp .env.production.example .env.production
chmod 600 .env.production
```

Required production values:

```env
FLOW_PROVIDER_ENV=production
FLOW_PROVIDER_PUBLIC_BASE_URL=https://provider.example.com
FLOW_PROVIDER_BOOTSTRAP_API_KEY=fpa_prod_<business-secret>
FLOW_PROVIDER_EXTENSION_API_KEY=fpe_prod_<different-connector-secret>
CLOUDFLARE_TUNNEL_TOKEN=<token>
```

`FLOW_PROVIDER_BOOTSTRAP_API_KEY` authenticates trusted backend callers to `/v1/*`. `FLOW_PROVIDER_EXTENSION_API_KEY` authenticates only the private Chrome connector. They must be strong, non-placeholder values and must be different.

Copy `extension/config.local.example.js` to the Git-ignored `extension/config.local.js` and configure the extension key there before packaging/loading the connector. Never expose either production key in a frontend application.

The production Compose service does **not** publish port `8000` on the VPS host. External and host-local applications should normally use the Cloudflare HTTPS hostname. Do not document or depend on a raw public VPS port unless the Compose topology is intentionally changed and separately secured.

## Cloudflare

The tunnel forwards the Provider hostname to `http://api:8000` inside the Compose network. Do not put an interactive browser challenge in front of `/v1/*` or `/api/extensions/ws`, because server-to-server calls and the Chrome connector cannot solve such a challenge. Normal WAF/DDoS controls remain appropriate.

## Deploy

```bash
bash scripts/deploy-production.sh
```

The helper:

1. validates Compose interpolation;
2. builds the API and starts API + tunnel;
3. waits for container liveness (`/health/live`);
4. verifies the current OpenAPI surface;
5. verifies an unauthenticated `/v1/jobs/status` call is rejected with `401 INVALID_API_KEY`;
6. verifies the configured business key can call `/v1/jobs/status`;
7. verifies the legacy `/admin` surface is absent.

GitHub production deployment additionally validates the business and extension secrets exist and differ before running the helper.

## Verify

```bash
curl -fsS https://provider.example.com/health/live
curl -fsS https://provider.example.com/health/ready
```

`/health/live` proves the API process is alive. `/health/ready` returns `200` only when the store and at least one signed-in Google Flow connector are ready; otherwise it returns `503`.

A business endpoint must be called with Bearer auth:

```bash
curl -sS https://provider.example.com/v1/jobs/status \
  -H "Authorization: Bearer $FLOW_PROVIDER_BOOTSTRAP_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"job_ids":["smoke_missing"]}'
```

A missing job returning `JOB_NOT_FOUND` confirms authenticated request routing without spending credits.

## Release acceptance

CI and deployment smoke are necessary but are not the final Google Flow acceptance test. Before declaring a release production-ready, use a real signed-in Chrome profile and verify at least:

1. `/health/ready` becomes `ready`;
2. one image generation reaches `complete`;
3. one paid video generation reaches `complete` and its result is downloadable;
4. reusing the same `Idempotency-Key` does not create a duplicate paid request;
5. a service restart preserves durable job/status state and does not duplicate an uncertain paid dispatch.

Back up the `provider_data` volume before destructive migration. Restore the SQLite DB and `/data/assets` together; restoring only the DB can leave persisted references without their source bytes.
