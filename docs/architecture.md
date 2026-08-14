# Architecture

```text
HTTP client -> fixed FlowProviderAPI endpoints -> Chrome extension -> Google Flow
                    auth + routing       browser auth/captcha
```

The process uses a small SQLite project store and no worker or asset service. Live Chrome connections, account load, cooldowns and in-flight HTTP/RPC state remain in memory.

For a request without a routing scope, the API fills the oldest ready Chrome installation up to three concurrent HTTP jobs before moving to the next installation. A reservation is held across every extension RPC in the job. Video jobs additionally reserve at least 20 credits, or their higher known Omni cost, preventing concurrent jobs from reusing the same visible balance. After every paid attempt the cached balance is cleared and refreshed with managed retries. Cooldown, unhealthy, disconnected, full, and under-funded installations are excluded. The extension injects browser-owned Google authentication and captcha.

Managed image generation accepts no `project_id`. On the first request for an installation/account pair, the Provider lists projects once to recover an existing project titled `FlowProvider`, or creates it when absent, then stores the mapping in SQLite. Later requests reuse the stored project without listing again. Inline references are hashed and matched only within the selected installation/account/project, then reuse the cached media ID without a preflight network request. A stale-media `404` invalidates the cache and retries with a fresh upload. Explicit `project_id` and routing-scope requests remain available as a compatibility contract.

When an explicit `project_id` matches a managed project in the store, the Provider automatically routes upload, image, and video generation to the owning installation. Repeated `/v1/media` uploads use the same account/project-scoped media cache. Unknown external projects still require the compatibility routing scope.

## Sticky routing for project-scoped Flow media

Google Flow projects and media belong to the Google account/browser profile that created them. To keep a multi-request Flow sequence on the same account, FlowProviderAPI supports a stateless routing token in the `X-Provider-Routing-Scope` HTTP header.

`POST /v1/projects` selects a ready extension normally and returns `X-Provider-Routing-Scope` in the response headers. The caller should persist that opaque value together with the returned Google Flow `project_id`, then send the same header on `/v1/media`, `/v1/images/generations`, `/v1/videos/generations`, and `/v1/videos/status` for that project/workflow.

The v2 routing scope is an HMAC-signed token derived from the extension installation identity plus normalized Google account email. It contains no Google credential and requires no Provider database. A reconnect using the same installation/account can continue using the same scope, while signing another Google account into that installation invalidates the old route. Rotating the Provider bootstrap API key intentionally invalidates existing routing scopes; v1 installation-only scopes are rejected.

When a supplied scope cannot be served because its bound extension installation is offline, unhealthy, or has no free slot, the API returns `503 ROUTING_SCOPE_UNAVAILABLE`. It never falls back to another Google account for a scoped request because a different account may not own the referenced project, media, or operation.

For managed image generation, FlowProviderAPI decides when to create or recover the account's default project. In compatibility mode, explicit project/media bindings and cross-request recovery remain responsibilities of the integrating application.

On the first managed request for each extension connection and Google account identity, the Provider refreshes Google's newest-first project list and selects the first project named `FlowProvider`. The durable mapping is then reused for the rest of that connection session. Reconnecting or changing the signed-in account forces another refresh, so an older database mapping cannot permanently shadow a newer project.

The store persists project mappings plus SHA-256-to-Google-media-ID mappings scoped by installation ID, normalized Google account email, and project. Signing a different Google account into the same extension creates a separate namespace and cannot reuse the prior account's project/media IDs. The store never contains Google bearer tokens, cookies, captcha tokens, generated media bytes, or user asset bytes.

Completed video polls resolve Flow's cookie-protected media redirect through the owning extension and attach the expiring URL as both `downloadUrl` and `video.generatedVideo.fifeUrl`. Signed URLs are not persisted.

Video generation stores every returned operation name with its account and project. A status request groups operation names by account, polls owning extensions concurrently, and merges the upstream list results in caller order. Unknown operations require an account-bound routing scope rather than being sent to an arbitrary account.
