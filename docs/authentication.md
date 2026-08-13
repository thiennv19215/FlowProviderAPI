# Authentication

Provider clients use Bearer API keys. Keys are stored only as SHA-256 hashes. Create a client with:

```bash
python scripts/create_api_client.py FlowCanvas --priority 50 --max-concurrent 10
```

Never expose provider API keys in a browser frontend. Application backends should call FlowProviderAPI server-to-server.

Bearer API keys are client credentials only. Provider administration uses a separate `FLOW_PROVIDER_ADMIN_API_KEY` and `X-Admin-Key` header; it is not available through a client Bearer key.

## Issue and revoke client keys

An administrator can issue a key for a user or consuming application:

```bash
curl -X POST http://localhost:8000/v1/api-clients \
  -H "X-Admin-Key: $FLOW_PROVIDER_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"FlowCanvas user","max_concurrent_jobs":5,"rate_limit_per_minute":120}'
```

The response contains `api_key` exactly once. Only its SHA-256 hash is stored. Give the key to the user through a secure channel; it cannot be recovered later.

List clients without exposing their keys:

```bash
curl http://localhost:8000/v1/api-clients \
  -H "X-Admin-Key: $FLOW_PROVIDER_ADMIN_API_KEY"
```

Revoke a client while preserving its historical jobs and media:

```bash
curl -X DELETE http://localhost:8000/v1/api-clients/cli_... \
  -H "X-Admin-Key: $FLOW_PROVIDER_ADMIN_API_KEY"
```
