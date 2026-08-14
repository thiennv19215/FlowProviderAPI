# Provider routing scope

FlowProviderAPI persists only managed project mappings and image-hash-to-media-ID mappings. It does not persist credentials, image bytes, jobs, or application workflow state.

Google Flow projects and media are account-scoped. In a multi-account deployment, all requests that reuse one Flow `project_id`, `media_id`, or video operation must be routed back to the same Chrome installation/account that created them.

Managed image-generation requests omit `project_id` and send optional Base64 references through `input_images`. For that flow, the Provider owns account selection and project reuse, so the caller does not need to store or return a routing scope. The scope contract below applies only to compatibility endpoints where callers explicitly reuse Google project or media IDs across requests.

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
POST /v1/videos/status
X-Provider-Routing-Scope: <opaque-signed-token>
```

Successful scoped responses repeat the same `X-Provider-Routing-Scope` header for convenience.

## Failure behavior

If the token is malformed or has an invalid signature, the API returns:

```text
400 ROUTING_SCOPE_INVALID
```

If the bound Chrome installation is offline, unhealthy, or currently has no free slot, the API returns:

```text
503 ROUTING_SCOPE_UNAVAILABLE
```

A scoped request never falls back to another available Google account. The caller decides whether to retry later or create a new Flow project on another account and re-upload any required media.

## Security and lifecycle

The routing scope is opaque to callers and contains no Google credential. It is HMAC-signed using the Provider bootstrap API key and encodes the extension installation identity only. No database is required.

The extension `installationId` is stable across WebSocket reconnects, so reconnecting the same installation can continue serving existing routing scopes. Rotating `FLOW_PROVIDER_BOOTSTRAP_API_KEY` invalidates previously issued scopes.

## Responsibility boundary

For managed image generation, FlowProviderAPI owns connection selection, project lookup, media-ID deduplication, browser authentication/captcha injection, and forwarding. The compatibility contract additionally supports caller-managed sticky routing.

The integrating application owns durable state such as:

- which workflow/board/run uses which routing scope;
- the Google Flow `project_id` paired with that scope;
- local asset identity and storage;
- provider media bindings created outside the managed image-generation flow;
- failover to another account/project when a routing scope becomes unavailable.

This keeps FlowProviderAPI usable as a third-party endpoint while allowing callers to safely reuse Google Flow media in multi-account deployments.
