# FlowProvider API — backend integration

Base URL: `https://api.shopcongngheso5.io.vn`

This API is for server-to-server use. Keep the API key on your backend; never
ship it to a browser or mobile app.

```http
Authorization: Bearer <FLOW_PROVIDER_API_KEY>
Content-Type: application/json
```

Every key owns its own tasks and media. A different key receives `404` for a
task or media item it does not own.

## Core rules

- Generation requests are asynchronous and return `202 Accepted` plus a
  server-generated `task_id`.
- `media_id` is a 15-digit **string**, not a JSON number. Preserve it exactly.
- Poll only while task status is `queued` or `running`.
- Terminal statuses are `done`, `failed`, and `canceled`.
- Results expose stable Provider API URLs. They require the same Bearer key and
  redirect to a short-lived R2 URL; do not send the Bearer header to R2.

```ts
type MediaId = string;
type TaskStatus = "queued" | "running" | "done" | "failed" | "canceled";

type TaskOutput = {
  media_id: MediaId;
  type: "image" | "video";
  url: string | null;
  thumbnail_url?: string | null;
};

type Task = {
  task_id: string;
  status: TaskStatus;
  outputs: TaskOutput[];
  error: ApiError | null;
};

type ApiError = {
  status_code: number;
  code: string;
  message: string;
  details: Array<{ field: string | null; code: string; message: string }>;
  request_id: string | null;
  retryable: boolean;
};
```

## 1. Upload a reference image

Use this only for a file your application already owns. Generated outputs can
be referenced directly by their returned `media_id`; do not download and
re-upload them.

```bash
curl -X POST 'https://api.shopcongngheso5.io.vn/v1/media' \
  -H "Authorization: Bearer $FLOW_PROVIDER_API_KEY" \
  -F 'file=@reference.png;type=image/png'
```

Response (`201`):

```json
{
  "media_id": "123456789012345",
  "object": "media",
  "type": "image",
  "status": "done",
  "mime_type": "image/png",
  "size_bytes": 182731,
  "width": null,
  "height": null,
  "duration": null,
  "url": "https://api.shopcongngheso5.io.vn/media/123456789012345",
  "created_at": "2026-08-13T00:00:00Z"
}
```

Use `GET /v1/media/{media_id}` to retrieve this metadata later.

## 2. Create an image

`POST /v1/images/generations`

```json
{
  "prompt": "A premium cobalt-blue glass perfume bottle on a white pedestal",
  "model": "banana_pro",
  "aspect_ratio": "9:16",
  "output_count": 1,
  "reference_media_ids": ["123456789012345"]
}
```

`model` is `banana_pro` or `banana_2`. `aspect_ratio` is `1:1`, `16:9`, or
`9:16` (default).

## 3. Create image-to-video

`POST /v1/videos/image-to-video`

```json
{
  "prompt": "The bottle slowly rotates as light moves across the glass.",
  "start_media_id": "123456789012345",
  "quality": "lite",
  "aspect_ratio": "9:16"
}
```

`quality` is `lite`, `fast`, `quality`, `lite_relaxed`, or `fast_relaxed`.

## 4. Create Omni video from one or more images

`POST /v1/videos/omni-generations`

```json
{
  "prompt": "The rooster takes two natural steps, turns its head, and its feathers move in a gentle breeze.",
  "reference_media_ids": ["123456789012345"],
  "duration": 4,
  "aspect_ratio": "9:16"
}
```

`duration` is `2`, `4`, `8`, or `10` seconds. Use the `media_id` from either
an upload or a completed image task. The Provider retains the internal Flow
mapping, so a generated image is reused without an extra client upload.

## 5. Poll a task

All three generation endpoints return the same shape (`202`):

```json
{
  "task_id": "job_abc123",
  "status": "queued",
  "outputs": [],
  "error": null
}
```

Persist `task_id`, then poll every 3–5 seconds:

```http
GET /v1/status/{task_id}
Authorization: Bearer <FLOW_PROVIDER_API_KEY>
```

Completed Omni response:

```json
{
  "task_id": "job_abc123",
  "status": "done",
  "outputs": [
    {
      "media_id": "234567890123456",
      "type": "video",
      "url": "https://api.shopcongngheso5.io.vn/media/234567890123456",
      "thumbnail_url": "https://api.shopcongngheso5.io.vn/media/234567890123456/thumbnail"
    }
  ],
  "error": null
}
```

Use `GET /v1/status?limit=20&status=done&type=video` to list only the
caller-owned tasks. Cancel pending work with `POST /v1/status/{task_id}/cancel`.

## 6. Deliver media to users

The URL in a task result is an authenticated API URL:

```http
GET /media/{media_id}
Authorization: Bearer <FLOW_PROVIDER_API_KEY>
```

For video thumbnails use:

```http
GET /media/{media_id}/thumbnail
Authorization: Bearer <FLOW_PROVIDER_API_KEY>
```

The API responds with `307 Temporary Redirect` to a signed R2 URL, normally
valid for 15 minutes. A server HTTP client following redirects must remove the
Provider `Authorization` header before the R2 request. For a browser client,
have your backend proxy the content or store it in application-owned storage;
do not expose the Provider API key.

## Errors and retries

Immediate errors use this envelope:

```json
{
  "error": {
    "status_code": 503,
    "code": "PROVIDER_ACCOUNT_UNAVAILABLE",
    "message": "No Google Flow account is currently online.",
    "details": [],
    "request_id": "req_...",
    "retryable": true
  }
}
```

- `401 INVALID_API_KEY`: missing or invalid Bearer key.
- `403`: caller is not allowed by the Provider gateway/WAF.
- `404 JOB_NOT_FOUND` or `MEDIA_NOT_FOUND`: wrong ID or another key owns it.
- `429`: rate-limited; retry according to your own backoff policy.
- `503 PROVIDER_ACCOUNT_UNAVAILABLE`: no ready Flow account; retry later.

A known task whose upstream work failed still returns HTTP `200` from
`GET /v1/status/{task_id}` with `status: "failed"` and its nested `error`.
Always inspect both.

## Optional unified endpoint

`POST /v1/generations` supports `kind`, `media_ids`, and `options` for
orchestrators that want one endpoint for image, video, and Omni. It requires an
`Idempotency-Key` header. For most integrations, the three native endpoints
above are clearer and require no idempotency header.
