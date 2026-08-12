# Authentication

Provider clients use Bearer API keys. Keys are stored only as SHA-256 hashes. Create a client with:

```bash
python scripts/create_api_client.py FlowCanvas --priority 50 --max-concurrent 10
```

Never expose provider API keys in a browser frontend. Application backends should call FlowProviderAPI server-to-server.

Bearer API keys are client credentials only. Provider administration uses a separate `FLOW_PROVIDER_ADMIN_API_KEY` and `X-Admin-Key` header; it is not available through a client Bearer key.
