# Architecture

```text
HTTP client -> fixed FlowProviderAPI endpoints -> Chrome extension -> Google Flow
                    auth + routing       browser auth/captcha
```

The process creates no SQL engine, worker, asset service or durable Provider record. Only live Chrome connections and in-flight HTTP/RPC state exist in memory.

Each call selects a ready connection with an available slot and forwards the requested HTTP operation. The extension injects browser-owned Google authentication and captcha. FlowProviderAPI returns the upstream status/body without a business-level response transformation.

Legacy database, worker, media and V1 routes have been removed from the repository and dependency graph.
