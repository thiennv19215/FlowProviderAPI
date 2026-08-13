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

Application backends should use the unified generation endpoint with one stable `Idempotency-Key` for each logical submission. Retrying the same normalized unified request with the same API client and key returns the same durable task; reusing that key for a different submission returns `409 IDEMPOTENCY_KEY_CONFLICT`.

## Create a generation

```http
POST /v1/generations
Idempotency-Key: flowcanvas:42:image:0
X-Request-Id: flowcanvas:42:image:0
```

Example:

```json
{
  "kind": "image",
  "prompt": "A cinematic glass perfume bottle",
  "media_ids": [],
  "options": {
    "model": "banana_pro",
    "aspect_ratio": "9:16",
    "output_count": 1
  }
}
```

Compatibility endpoints remain available:

- `POST /v1/images/generations`
- `POST /v1/videos/image-to-video`
- `POST /v1/videos/omni-generations`

The response is `202 Accepted` and contains a server-generated `task_id`. Persist it immediately. If the initial unified submit response is ambiguous, retry the exact same logical request with the same `Idempotency-Key` rather than minting a new key.

## Poll generation status

```http
GET /v1/status/{task_id}
```

Poll while `status` is `queued` or `running`. Stop on `done`, `failed`, or `canceled`.

```json
{
  "task_id": "job_xxx",
  "status": "done",
  "outputs": [
    {
      "media_id": "123456789012345",
      "type": "image",
    "url": "https://api.shopcongngheso5.io.vn/media/123456789012345"
    }
  ],
  "error": null
}
```

Ownership is derived from the Bearer key. A different client cannot read another client's generation status.

Use `GET /v1/status` to list statuses. `status` only accepts `queued`, `running`, `done`, `failed`, `canceled`; `type` only accepts `image`, `video`, `omni`.

## Upload your own media

```bash
curl -X POST https://api.shopcongngheso5.io.vn/v1/media \
  -H "Authorization: Bearer $FLOW_API_KEY" \
  -F 'file=@reference.png;type=image/png'
```

The returned `media_id` is a 15-digit JSON string. Use it in `media_ids`, `reference_media_ids`, or `start_media_id` depending on the generation endpoint. Generated image output IDs can be passed directly to image-to-video or Omni; do not upload that image again.

## Media delivery

Task output URLs are authenticated Provider API URLs, not direct Flow URLs. Call
`GET /media/{media_id}` with the same Bearer key; the API redirects to a
short-lived R2 download URL. Video task outputs also include
`/media/{media_id}/thumbnail`. Keep the key server-side.

## Cancellation

```http
POST /v1/status/{task_id}/cancel
```

Cancellation is cooperative. Work that has already been dispatched upstream may continue at Google Flow.

## Errors

Synchronous errors use the standard `error` envelope. Asynchronous provider failures appear in the generation status response's nested `error` field. A known generation status lookup remains HTTP `200`; inspect its `status` and `error`.

## Administration

Bearer API keys are business-client credentials only. Extension/provider administration uses a separate `X-Admin-Key`. Do not put either secret in browser JavaScript or a mobile bundle.
