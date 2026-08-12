# Backend integration reference

Stable HTTP contract for server-to-server integration.

Base URL: `https://api.shopcongngheso5.io.vn`
Authentication: `Authorization: Bearer <FLOW_PROVIDER_API_KEY>`

Keep the API key on your server only. A Bearer key owns its tasks and media; another key cannot read them.

## Contract rules

- Generation calls are asynchronous: they return `202 Accepted` with a server-generated `task_id`.
- Poll `GET /v1/tasks/{task_id}` every 3–5 seconds while `status` is `queued` or `running`.
- `media_id` is a 15-digit JSON **number**. Send it as a number, never as the legacy `media_...` string.
- `task_id` is an opaque string.
- Pass `X-Request-Id` to correlate logs; the response returns the same header.

```ts
type MediaId = number;

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

Send the file in multipart field `file`; `type` is optional (`image` or `video`) and must match the MIME type.

```bash
curl -X POST https://api.shopcongngheso5.io.vn/v1/media \
  -H "Authorization: Bearer $FLOW_PROVIDER_API_KEY" \
  -F 'file=@reference.png;type=image/png'
```

Response: `201 Created`.

```json
{
  "media_id": 123456789012345,
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

## Create an image

```http
POST /v1/images/generations
Content-Type: application/json
Authorization: Bearer <API_KEY>
```

```json
{
  "prompt": "A premium blue perfume bottle on a white pedestal",
  "model": "banana_pro",
  "aspect_ratio": "9:16",
  "output_count": 1,
  "reference_media_ids": [123456789012345]
}
```

| Field | Required | Values | Default |
|---|---:|---|---|
| `prompt` | Yes | 1–12,000 characters | — |
| `model` | No | `banana_pro`, `banana_2` | `banana_pro` |
| `aspect_ratio` | No | `1:1`, `16:9`, `9:16` | `9:16` |
| `output_count` | No | 1–4 | 1 |
| `reference_media_ids` | No | Up to 8 image IDs | `[]` |

## Create a video from an image

```http
POST /v1/videos/image-to-video
Content-Type: application/json
Authorization: Bearer <API_KEY>
```

```json
{
  "prompt": "Slow vertical camera push-in, soft reflections",
  "start_media_id": 123456789012345,
  "quality": "lite",
  "aspect_ratio": "9:16"
}
```

`quality`: `lite`, `fast`, `quality`, `lite_relaxed`, or `fast_relaxed`.
`aspect_ratio`: `16:9` or `9:16`.

Video is assigned only to an account with enough credit. If capacity is temporarily unavailable, the task remains `queued` with retryable `PROVIDER_ACCOUNT_UNAVAILABLE`; keep polling the same task rather than submitting a duplicate.

## Create Omni video

```http
POST /v1/videos/omni-generations
Content-Type: application/json
Authorization: Bearer <API_KEY>
```

```json
{
  "prompt": "The referenced objects assemble into a cinematic scene",
  "reference_media_ids": [123456789012345, 234567890123456],
  "duration": 4,
  "aspect_ratio": "9:16"
}
```

`reference_media_ids` requires 1–8 image IDs. `duration` is `2`, `4`, `8`, or `10`; `aspect_ratio` is `16:9` or `9:16`.

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
    "media_id": 345678901234567,
    "type": "video",
    "url": "https://flow-content.google/...",
    "thumbnail_url": "https://flow-content.google/..."
  }],
  "error": null
}
```

`thumbnail_url` is returned only when Google Flow provides a video thumbnail. Generated URLs are upstream-owned and may expire; copy successful output to your own durable storage if it must persist.

Other task endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /v1/tasks/{task_id}/cancel` | Cooperative cancellation; upstream Flow work may already be dispatched. |
| `GET /v1/tasks?limit=20&after={task_id}&status=&type=` | List caller-owned tasks; `limit` max 100, `type` is `image`, `video`, or `omni`. |

## Error contract

Synchronous HTTP errors use one envelope:

```json
{
  "error": {
    "status_code": 422,
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": [{"field":"start_media_id","code":"MISSING","message":"Field required"}],
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

Flow provider failures occur inside task polling: task lookup returns `200`, but has `status: "failed"` and `error` such as `429 RESOURCE_EXHAUSTED` or `403 PERMISSION_DENIED`.

## Integration checklist

1. Store `task_id` immediately after `202`.
2. Store `media_id` as numeric-safe value; submit it back as a JSON number.
3. Poll the existing task; do not create a new task for retryable queued video capacity.
4. Log `X-Request-Id` and task `error.request_id`.
5. Copy completed direct output URLs to your own storage if long-term retention is needed.

For UI behavior, see [frontend-integration.md](frontend-integration.md). For the shorter overview, see [integration.md](integration.md).
