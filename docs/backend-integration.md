# Backend integration reference

Stable HTTP contract for server-to-server integration.

Base URL: `https://api.shopcongngheso5.io.vn`
Authentication: `Authorization: Bearer <FLOW_PROVIDER_API_KEY>`

Keep the API key on your server only. A Bearer key owns its tasks and media; another key cannot read them.

## Contract rules

- Generation calls are asynchronous and return `202 Accepted` with a server-generated `task_id`.
- Poll `GET /v1/tasks/{task_id}` every 3–5 seconds while `status` is `queued` or `running`.
- `media_id` is a **15-digit JSON string** such as `"123456789012345"`.
- `task_id` is an opaque string.
- Pass `X-Request-Id` to correlate logs; the response returns the same header.
- Application orchestrators should prefer the unified `POST /v1/generations` contract. Provider-specific aliases/defaults are normalized inside FlowProviderAPI.

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

type Task = {
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

```http
POST /v1/media
Authorization: Bearer <API_KEY>
Content-Type: multipart/form-data
```

Send the file in multipart field `file`; `type` is optional (`image` or `video`) and must match the MIME type. Re-uploading identical ready content for the same API client and media type returns the existing media object.

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

Use `GET /v1/media/{media_id}` for metadata. The `url` for an uploaded reference needs the same Bearer header when downloading bytes.

## Unified generation contract

```http
POST /v1/generations
Content-Type: application/json
Authorization: Bearer <API_KEY>
```

Image example:

```json
{
  "kind": "image",
  "prompt": "A premium blue perfume bottle on a white pedestal",
  "media_ids": ["123456789012345"],
  "options": {
    "model": "banana_pro",
    "aspect_ratio": "9:16",
    "output_count": 1
  }
}
```

Video example:

```json
{
  "kind": "video",
  "prompt": "Slow vertical camera push-in, soft reflections",
  "media_ids": ["123456789012345"],
  "options": {
    "quality": "lite",
    "aspect_ratio": "9:16"
  }
}
```

Omni example:

```json
{
  "kind": "omni",
  "prompt": "The referenced objects assemble into a cinematic scene",
  "media_ids": ["123456789012345", "234567890123456"],
  "options": {
    "duration": 4,
    "aspect_ratio": "9:16"
  }
}
```

`kind` is `image`, `video`, or `omni`. The Provider owns model/aspect/quality normalization, account scheduling/capacity, Google Flow project/media mapping, and retry behavior.

The legacy native endpoints remain available for compatibility:

- `POST /v1/images/generations`
- `POST /v1/videos/image-to-video`
- `POST /v1/videos/omni-generations`

New application backends should use `/v1/generations` so they do not couple their orchestration layer to provider-specific endpoint shapes.

## Task response and polling

All generation endpoints return this shape with `202`:

```json
{"task_id":"job_abc123","status":"queued","outputs":[],"error":null}
```

Poll:

```http
GET /v1/tasks/{task_id}
Authorization: Bearer <API_KEY>
```

Successful video example:

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

`thumbnail_url` is returned only when Google Flow provides one. Generated URLs are upstream-owned and may expire; copy successful output to your own durable storage if it must persist.

A queued/running task remains the same task while FlowProviderAPI handles capacity and retry. Do not submit a duplicate task merely because dispatch is delayed.

Other task endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /v1/tasks/{task_id}/cancel` | Cooperative cancellation; upstream work may already be dispatched. |
| `GET /v1/tasks?limit=20&after={task_id}&status=&type=` | List caller-owned tasks; `limit` max 100. |

## Error contract

Synchronous HTTP errors use one envelope:

```json
{
  "error": {
    "status_code": 422,
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": [{"field":"media_ids","code":"MISSING","message":"Field required"}],
    "request_id": "req_...",
    "retryable": false
  }
}
```

| Status | Typical code | Action |
|---:|---|---|
| 400 | `INVALID_JSON` | Fix payload. |
| 401 | `INVALID_API_KEY` | Fix/rotate server secret. |
| 404 | `MEDIA_NOT_FOUND`, `JOB_NOT_FOUND` | Check ID and ownership. |
| 413 | `MEDIA_TOO_LARGE`, `REFERENCE_MEDIA_TOO_LARGE` | Reduce media size. |
| 422 | `VALIDATION_ERROR`, `INVALID_MEDIA_REFERENCE`, `INVALID_MEDIA_TYPE` | Fix request/reference. |
| 429 | `RATE_LIMIT_EXCEEDED` | Back off; respect `Retry-After` if supplied. |
| 5xx | `INTERNAL_ERROR` or provider issue | Retry only when `retryable` is true; log `request_id`. |

Provider failures can also occur inside task polling: task lookup returns `200` with terminal `status: "failed"` and a structured `error`.

## Integration checklist

1. Store `task_id` immediately after `202`.
2. Store `media_id` as a 15-digit string and send it back as a JSON string.
3. Poll the existing task; do not create duplicate jobs for delayed capacity.
4. Log `X-Request-Id` and task `error.request_id`.
5. Copy completed direct output URLs to durable application storage when persistence is required.

For the minimal application-orchestrator boundary, see [orchestrator-contract.md](orchestrator-contract.md).
