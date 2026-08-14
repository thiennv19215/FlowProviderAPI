# Architecture

```text
HTTP client -> fixed FlowProviderAPI endpoints -> Chrome extension -> Google Flow
                    auth + routing       browser auth/captcha
```

The process creates no SQL engine, worker, asset service or durable Provider record. Only live Chrome connections and in-flight HTTP/RPC state exist in memory.

For a request without a routing scope, the API selects a ready Chrome connection with an available slot and forwards the requested HTTP operation. The extension injects browser-owned Google authentication and captcha. FlowProviderAPI returns the upstream status/body without a business-level response transformation.

## Sticky routing for project-scoped Flow media

Google Flow projects and media belong to the Google account/browser profile that created them. To keep a multi-request Flow sequence on the same account, FlowProviderAPI supports a stateless routing token in the `X-Provider-Routing-Scope` HTTP header.

`POST /v1/projects` selects a ready extension normally and returns `X-Provider-Routing-Scope` in the response headers. The caller should persist that opaque value together with the returned Google Flow `project_id`, then send the same header on `/v1/media`, `/v1/images/generations`, `/v1/videos/generations`, and `/v1/videos/status` for that project/workflow.

The routing scope is an HMAC-signed token derived from the extension installation identity. It contains no Google credential and requires no Provider database. A reconnect from the same extension installation can continue using the same scope. Rotating the Provider bootstrap API key intentionally invalidates existing routing scopes.

When a supplied scope cannot be served because its bound extension installation is offline, unhealthy, or has no free slot, the API returns `503 ROUTING_SCOPE_UNAVAILABLE`. It never falls back to another Google account for a scoped request because a different account may not own the referenced project, media, or operation.

FlowProviderAPI does not decide when to create a new project, migrate media between projects/accounts, retry on another account, or persist media bindings. Those are responsibilities of the integrating application.

Legacy database, worker, media and V1 routes have been removed from the repository and dependency graph.
