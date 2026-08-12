# Backend integration reference

Stable HTTP contract for server-to-server integration.

Base URL: `https://api.shopcongngheso5.io.vn`
Authentication: `Authorization: Bearer <FLOW_PROVIDER_API_KEY>`

Keep the API key on your server only. A Bearer key owns its generation statuses and media; another key cannot read them.

## Contract rules

- Generation calls are asynchronous and return `202 Accepted` with a server-generated `task_id`.
- Every generation POST is a new submission. `Idempotency-Key` is not part of V1.
- Poll `GET /v1/status/{task_id}` every 3–5 seconds while `status` is `queued` or `running`.
- `media_id` is a **15-digit JSON string** such as `"123456789012345"`.
- `task_id` is an opaque string.
- Pass `X-Request-Id` to correlate logs; the response returns the same header.
- Application orchestrators should prefer `POST /v1/generations`.

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
  status: "queued" | "running" | "succeeded" | "failed" | "canceled";
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
  "status": "ready",
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

Compatibility generation endpoints remain available:

- `POST /v1/images/generations`
- `POST /v1/videos/image-to-video`
- `POST /v1/videos/omni-generations`

## Status polling

All generation endpoints return `202` with:

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
  "status": "succeeded",
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

`status` accepts only `queued`, `running`, `succeeded`, `failed`, `canceled`. `type` accepts only `image`, `video`, `omni`; invalid values return validation errors instead of an empty list.

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

1. Store `task_id` immediately after `202`.
2. Store `media_id` as a 15-digit string.
3. Poll `/v1/status/{task_id}`; do not resubmit a generation merely because capacity is delayed.
4. If your own product needs deduplication, implement it in your application layer before calling FlowProviderAPI.
5. Log `X-Request-Id` and nested error `request_id`.
6. Copy direct output URLs to durable storage when persistence is required.
