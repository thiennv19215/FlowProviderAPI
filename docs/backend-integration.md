# Backend integration reference

Stable HTTP contract for server-to-server integration.

Base URL: `https://api.shopcongngheso5.io.vn`
Authentication: `Authorization: Bearer <FLOW_PROVIDER_API_KEY>`

Keep the API key on your server only. A Bearer key owns its generation statuses and media; another key cannot read them.

## Contract rules

- Generation calls are asynchronous and return `202 Accepted` with a server-generated `task_id`.
- Application orchestrators should prefer `POST /v1/generations` and send a stable `Idempotency-Key` for each logical submission.
- Retrying the same normalized unified submission with the same API client and key returns the same durable task; reusing that key for a different normalized submission returns `409 IDEMPOTENCY_KEY_CONFLICT`.
- Poll `GET /v1/status/{task_id}` every 3–5 seconds while `status` is `queued` or `running`.
- `media_id` is a **15-digit JSON string** such as `"123456789012345"`.
- `task_id` is an opaque string.
- Pass `X-Request-Id` to correlate logs; the response returns the same header. FlowCanvas uses the same logical submission key for both `Idempotency-Key` and `X-Request-Id`.

```ts
type MediaId = string;

type ApiError = {
  status_code: number;
  code: string;
  message: string;
  details: Array<{ field: string | null; code: string; message: string }>;
  request_id: string | null;
  retryable: boolean;
};

type GenerationStatus = {
  task_id: string;
  status: "queued" | "running" | "done" | "failed" | "canceled";
  outputs: Array<{
    media_id: MediaId;
    type: "image" | "video";
    url: string | null;
    thumbnail_url?: string | null;
  }>;
  error: ApiError | null;
};
```

## Upload reference media

`POST /v1/media` accepts multipart field `file`; optional `type` must match the MIME type. Identical ready content for the same client and media type is content-deduplicated.

```json
{
  "media_id": "123456789012345",
  "object": "media",
  "type": "image",
  "status": "done",
  "mime_type": "image/png",
  "size_bytes": 182731,
  "url": "https://api.shopcongngheso5.io.vn/media/123456789012345",
  "created_at": "2026-08-12T12:00:00Z"
}
```

Use `GET /v1/media/{media_id}` for metadata. Uploaded media content URLs require the same Bearer authorization.

## Unified generation

```http
POST /v1/generations
Content-Type: application/json
Authorization: Bearer <API_KEY>
Idempotency-Key: flowcanvas:42:image:0
X-Request-Id: flowcanvas:42:image:0
```

```json
{
  "kind": "image",
  "prompt": "A premium blue perfume bottle",
  "media_ids": ["123456789012345"],
  "options": {
    "model": "banana_pro",
    "aspect_ratio": "9:16",
    "output_count": 1
  }
}
```

`kind` is `image`, `video`, or `omni`. Provider-specific normalization, account scheduling, Google Flow project/media mapping, capacity and retries remain internal.

If a unified generation POST times out after the Provider may have accepted it, retry the exact same logical submission with the same `Idempotency-Key`. Do not mint a new key for that retry.

Compatibility generation endpoints remain available:

- `POST /v1/images/generations`
- `POST /v1/videos/image-to-video`
- `POST /v1/videos/omni-generations`

The compatibility endpoints keep their native request shapes. The server-to-server idempotency guarantee described above belongs to the unified `/v1/generations` contract.

## Status polling

Generation endpoints return `202` with:

```json
{"task_id":"job_abc123","status":"queued","outputs":[],"error":null}
```

Poll:

```http
GET /v1/status/{task_id}
Authorization: Bearer <API_KEY>
```

Successful video:

```json
{
  "task_id": "job_abc123",
  "status": "done",
  "outputs": [{
    "media_id": "345678901234567",
    "type": "video",
    "url": "https://flow-content.google/...",
    "thumbnail_url": "https://flow-content.google/..."
  }],
  "error": null
}
```

Generated URLs are upstream-owned and may expire. Copy important outputs to durable application storage.

Other status endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /v1/status/{task_id}/cancel` | Cooperative cancellation. |
| `GET /v1/status?limit=20&after={task_id}&status=&type=` | List caller-owned generation statuses; `limit` max 100. |

`status` accepts only `queued`, `running`, `done`, `failed`, `canceled`. `type` accepts only `image`, `video`, `omni`; invalid values return validation errors instead of an empty list.

## Error contract

Synchronous errors use one envelope:

```json
{
  "error": {
    "status_code": 422,
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": [],
    "request_id": "req_...",
    "retryable": false
  }
}
```

Provider failures can occur inside status polling: `GET /v1/status/{task_id}` still returns HTTP `200` when a known generation has terminal `status: "failed"`; inspect its nested `error`.

## Integration checklist

1. Generate one stable `Idempotency-Key` per logical unified generation submission and persist the returned `task_id` immediately after `202`.
2. If the initial submit result is ambiguous, retry the same normalized request with the same key.
3. Store `media_id` as a 15-digit string.
4. Poll `/v1/status/{task_id}`; do not resubmit a generation merely because capacity is delayed.
5. Log `X-Request-Id` and nested error `request_id`.
6. Copy direct output URLs to durable storage when persistence is required.
