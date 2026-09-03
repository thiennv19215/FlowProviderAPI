# Architecture

```text
HTTP client -> fixed FlowProviderAPI endpoints -> Job Queue (SQLite) -> Chrome extension -> Google Flow
                     auth + routing + queue      Background Worker          browser auth/captcha
```

The process uses a durable SQLite store for projects, media mappings, operations, and image/video jobs. Live Chrome connections, account load, cooldowns, and in-flight RPC state remain in memory. Every job has `media_type` (`image` or `video`), `generation_type` (`image`, `image_to_video`, `i2v`, `omni`, etc.), and a public lifecycle of `queued -> running -> complete|failed`. Existing databases migrate the former `job_type` column to `generation_type` automatically.

Incoming video generation requests carry account/project-bound media references, so they are not persisted into the worker queue when no safe owning route is available; the API returns a retryable HTTP 503 instead. `provider_jobs` remains available for internally enqueued work. The background `JobWorker` claims such jobs atomically, refreshes credit state after every paid attempt, and dispatches them to Google Flow when an account with sufficient credits (>= 20-25 credits) becomes ready.

The API resolves the owning account/project and media references, persists a `queued` job, and returns its Provider job ID. `JobWorker` atomically claims both image and video work. Image generation completes in that one worker call and is never added to the upstream polling schedule. Video dispatch records a Flow poll identifier and the worker polls it with a durable schedule (`next_poll_at`, attempts, consecutive errors and latest error). `/v1/jobs/status` reads durable rows only and never consumes a live extension slot.

For a request without a routing scope, the API selects the least-loaded ready Chrome installation, breaking ties by connection age, with up to three concurrent HTTP jobs per installation. A reservation is held across every extension RPC in the job. Video jobs additionally reserve at least 20 credits, or their higher known Omni cost, preventing concurrent jobs from reusing the same visible balance. If an unscoped video request targets an underfunded project or Flow returns a deterministic insufficient-credit/quota rejection, the Provider makes one failover attempt on another eligible installation and rehydrates known media into that installation's project. After every paid attempt the cached balance is cleared and refreshed with managed retries. Cooldown, unhealthy, disconnected, full, and under-funded installations are excluded. The extension injects browser-owned Google authentication and captcha.

Managed image and video generation accept no `project_id` and receive caller-owned reference bytes through `input_images`. On the first request for an installation/account pair, the Provider lists projects once to recover the newest existing project, or creates a project titled `FlowProvider` when the account has no projects, then stores the mapping in SQLite. Later requests reuse the stored project without listing again. Inline references are hashed and matched only within the selected installation/account/project. Known media IDs from another managed account are rehydrated into the selected account's project before queueing. A stale-media `404` from the image worker invalidates that cache entry so the next request uploads it again. Explicit project/media bindings remain available for compatibility.

When an explicit `project_id` matches a managed project in the store, the Provider automatically routes upload, image, and video generation to the owning installation. A known media ID from another account can be rehydrated into that managed target project when no routing scope is supplied. Repeated `/v1/media` uploads use the same account/project-scoped media cache. Unknown external projects still require the compatibility routing scope.

## Sticky routing for project-scoped Flow media

Google Flow projects and media belong to the Google account/browser profile that created them. To keep a multi-request Flow sequence on the same account, FlowProviderAPI supports a stateless routing token in the `X-Provider-Routing-Scope` HTTP header.

`POST /v1/projects` selects a ready extension normally and returns `X-Provider-Routing-Scope` in the response headers. The caller should persist that opaque value together with the returned Google Flow `project_id`, then send the same header on `/v1/media`, `/v1/images/generations`, and `/v1/videos/generations` for that project/workflow.

The v2 routing scope is an HMAC-signed token derived from the extension installation identity plus normalized Google account email. It contains no Google credential and requires no Provider database. A reconnect using the same installation/account can continue using the same scope, while signing another Google account into that installation invalidates the old route. Rotating the Provider bootstrap API key intentionally invalidates existing routing scopes; v1 installation-only scopes are rejected.

When a supplied scope cannot be served because its bound extension installation is offline, unhealthy, or has no free slot, the API returns `503 ROUTING_SCOPE_UNAVAILABLE`. It never falls back to another Google account for a scoped request because a different account may not own the referenced project, media, or operation.

For managed image and video generation, FlowProviderAPI decides when to create or recover the account's default project and resolves inline image bytes to media IDs. In compatibility mode, explicit project/media bindings and cross-request recovery remain responsibilities of the integrating application.

On the first managed request for each extension connection and Google account identity, the Provider refreshes Google's project list and selects the newest project. The durable mapping is then reused for the rest of that connection session. Reconnecting or changing the signed-in account forces another refresh, so an older database mapping cannot permanently shadow a newer project.

The store persists project mappings plus SHA-256-to-Google-media-ID mappings scoped by installation ID, normalized Google account email, and project. Signing a different Google account into the same extension creates a separate namespace and cannot reuse the prior account's project/media IDs. SQLite never contains Google bearer tokens, cookies, captcha tokens, or generated media bytes. Uploaded Character source bytes are intentionally retained separately under `FLOW_PROVIDER_ASSET_STORE_PATH/<sha256>` with the configured orphan-retention policy.

Completed video polls resolve Flow's cookie-protected media redirects through the owning extension. The Provider attaches the expiring video and thumbnail URLs once at the media level as `downloadUrl` and `thumbnailUrl`. Upstream fields already returned by Flow remain untouched, and completed responses are cached durably in SQLite.

## Character references

Character workflows are domain-specific Provider endpoints over the same Flow image/video APIs. `POST /v1/media` stores uploaded image bytes content-addressed under `FLOW_PROVIDER_ASSET_STORE_PATH` and caches the account/project-specific Flow `media_id`. A Character stores up to three reference asset hashes and their current media IDs. `POST /v1/characters/{id}/images/generations` snapshots those assets and queues an image job; the worker resolves each asset into the selected Flow project and calls `flowMedia:batchGenerateImages` with `IMAGE_INPUT_TYPE_REFERENCE`. `POST /v1/characters/{id}/videos/generations` follows the same asset resolution but dispatches Omni R2V and enters the normal durable video poller. The generic image/video endpoints intentionally do not accept Character IDs. Character reference bytes survive Flow signed-URL expiry and account changes; unused assets are garbage-collected by the configured retention policy.

Video generation stores every returned operation name with its account and project. A status request groups operation names by account, polls owning extensions concurrently, and merges the upstream list results in caller order. Unknown operations require an account-bound routing scope rather than being sent to an arbitrary account.

Omni responses may return `workflows[].name` separately from `media[].workflowId`. The Provider correlates those fields and stores the workflow name with the primary media poll ID, so managed callers can poll by the workflow name without preserving a routing scope.
