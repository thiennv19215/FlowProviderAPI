# Provider routing scope

FlowProviderAPI persists only managed project mappings and image-hash-to-media-ID mappings. It does not persist credentials, image bytes, jobs, or application workflow state.

Google Flow projects and media are account-scoped. In a multi-account deployment, explicitly scoped requests that reuse one Flow `project_id`, `media_id`, or video operation must be routed back to the same Chrome installation/account that created them.

The Provider also records uploaded media ownership and uses a known `media_id` to recover its account/project route for video generation. Callers should forward the returned scope explicitly when they require a workflow to remain on one account. Without a routing scope, a known media ID may be copied into the managed account/project selected for a repeated request, including an explicitly selected managed `project_id`. When media is uploaded specifically for a paid video, `required_credits` lets account selection exclude connectors that cannot submit that video. A bounded failover attempt can pass prior `project_id` values in `excluded_project_ids`; the Provider then selects a different account and uploads fresh media there. For an unscoped video request, if the selected account is already below the required credit cost or Flow deterministically rejects the request for insufficient credits/quota, the Provider makes one failover attempt on another eligible account and rehydrates known media into its project. A routing scope always disables this automatic failover.

Managed image/video generation requests omit `project_id` and send Base64 references through `input_images`. For that flow, the Provider owns account selection and reuses the newest existing project on that Google account. It creates a `FlowProvider` project only when a complete lookup confirms that the account has no projects, so reconnects and concurrent requests do not spam projects. Inline image-to-video requires one image; Omni accepts one to eight combined inline/media-ID references. If a managed request references a media ID previously recorded by the Provider and the scheduler selects another account, the Provider downloads the source image through the owning account, uploads it into the selected account's project, and substitutes the copied media ID. The source bytes are not persisted. The caller does not need to store or return a routing scope. The scope contract below applies only to compatibility calls where callers explicitly reuse Google project or media IDs across requests.

## Contract

Create the Flow project normally:

```http
POST /v1/projects
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

The response body remains the upstream Google Flow response. FlowProviderAPI adds this response header:

```http
X-Provider-Routing-Scope: <opaque-signed-token>
```

Store the routing scope together with the returned `project_id` in the integrating application.

Send the same header on every follow-up request that belongs to that project/account context:

```http
POST /v1/media
X-Provider-Routing-Scope: <opaque-signed-token>
```

```http
POST /v1/images/generations
X-Provider-Routing-Scope: <opaque-signed-token>
```

```http
POST /v1/videos/generations
X-Provider-Routing-Scope: <opaque-signed-token>
```

```http
POST /v1/jobs/status
X-Provider-Routing-Scope: <opaque-signed-token>
```

Successful scoped responses repeat the same `X-Provider-Routing-Scope` header for convenience.

## Failure behavior

If the token is malformed or has an invalid signature, the API returns:

```text
400 ROUTING_SCOPE_INVALID
```

If the bound Chrome installation/Google-account pair is offline, changed, unhealthy, or currently has no free slot, the API returns:

```text
503 ROUTING_SCOPE_UNAVAILABLE
```

A scoped request never falls back to another available Google account and never performs an implicit cross-account media transfer. The caller decides whether to retry later or create a new Flow project on another account and re-upload any required media. An explicit `project_id` without a routing scope identifies the target project; known media from another account can be rehydrated into that project.

## Security and lifecycle

The v2 routing scope is opaque to callers and contains no Google credential. It is HMAC-signed using the Provider bootstrap API key and encodes the extension installation identity plus normalized Google account email. No database is required.

The extension `installationId` is stable across WebSocket reconnects, so reconnecting the same installation with the same Google account can continue serving existing routing scopes. Changing the signed-in Google account makes the old scope unavailable. Rotating `FLOW_PROVIDER_EXTENSION_API_KEY` invalidates previously issued scopes. Installation-only v1 scopes are intentionally rejected.

## Responsibility boundary

For managed image/video generation, FlowProviderAPI owns connection selection, project lookup, media-ID deduplication, known-media rehydration between managed accounts, browser authentication/captcha injection, and forwarding. The compatibility contract additionally supports caller-managed sticky routing.

For the compatibility contract, the integrating application owns durable state such as:

- which workflow/board/run uses which routing scope;
- the Google Flow `project_id` paired with that scope;
- local asset identity and storage;
- provider media bindings created outside the managed generation flow;
- failover to another account/project when a routing scope becomes unavailable.

This keeps FlowProviderAPI usable as a third-party endpoint while allowing callers to safely reuse Google Flow media in multi-account deployments.
