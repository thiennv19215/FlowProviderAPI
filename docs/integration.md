# FlowProvider API integration (current)

Base URL:

```text
https://api.shopcongngheso5.io.vn
```

All generation endpoints are asynchronous. Send the client Bearer key on every business request:

```http
Authorization: Bearer fpa_live_...
Content-Type: application/json
```

## Create an image

```bash
curl -X POST https://api.shopcongngheso5.io.vn/v1/images/generations \
  -H "Authorization: Bearer $FLOW_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"A cinematic glass perfume bottle","model":"banana_pro","aspect_ratio":"9:16"}'
```

The response is `202 Accepted` and contains `task_id`.

Supported image models are `banana_pro` and `banana_2`. Supported ratios are `1:1`, `16:9`, and `9:16` (default).

## Poll a task

```http
GET /v1/tasks/{task_id}
```

Poll while `status` is `queued` or `running`. Stop on `succeeded`, `failed`, or `canceled`. Polling does not use `Retry-After`; the caller chooses its interval (for example, five seconds).

Successful output:

```json
{
  "task_id": "task_xxx",
  "status": "succeeded",
  "outputs": [{"media_id": 123456789012345, "type": "image", "url": "https://flow-content.google/..."}],
  "error": null
}
```

Task ownership is derived from the Bearer key. A different client cannot read another client's task.

## Upload your own media

Use the Media resource for a file that will be referenced later. Upload the file in one request:

```bash
curl -X POST https://api.shopcongngheso5.io.vn/v1/media \
  -H "Authorization: Bearer $FLOW_API_KEY" \
  -F 'file=@reference.png;type=image/png'
```

The returned `media_id` is the media reference used by `reference_media_ids` and `start_media_id`. `GET /v1/media/{media_id}` returns metadata and its usable `url`.

## Use generated media as a reference

Pass one or more returned media IDs:

```json
{
  "prompt": "Create a new studio scene using these references",
  "model": "banana_pro",
  "aspect_ratio": "9:16",
  "reference_media_ids": [123456789012345, 234567890123456]
}
```

## Create a video

```bash
curl -X POST https://api.shopcongngheso5.io.vn/v1/videos/image-to-video \
  -H "Authorization: Bearer $FLOW_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Slow cinematic camera movement","start_media_id":123456789012345,"quality":"lite","aspect_ratio":"9:16"}'
```

Video results use the same `/v1/tasks/{task_id}` polling contract. A video output includes `thumbnail_url` when Google Flow provides a preview image; it is `null` for image output or when Flow has no preview. For multi-reference video use `/v1/videos/omni-generations` with `reference_media_ids` and `duration` (`2`, `4`, `8`, or `10`).

## Errors

Synchronous errors use the standard `error` envelope. Asynchronous provider errors appear in the task's `error` field, preserving upstream status/code/message (for example `429 RESOURCE_EXHAUSTED`). The task lookup itself remains HTTP `200`; inspect the task status.

## Administration

Bearer API keys are business-client credentials only. Extension/provider administration is a separate control plane using `X-Admin-Key` configured by `FLOW_PROVIDER_ADMIN_API_KEY`. Do not put either secret in a browser extension or frontend.
