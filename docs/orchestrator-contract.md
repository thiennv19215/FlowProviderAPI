# Orchestrator contract

FlowProviderAPI exposes a small server-to-server generation boundary for application backends such as FlowCanvas.

## Unified generation

`POST /v1/generations`

Send a stable `Idempotency-Key` header for each logical remote submission. Retrying the same logical submission with the same API client and key returns the same durable Provider task instead of creating duplicate generation work. Reusing the same key for a different normalized submission returns `409 IDEMPOTENCY_KEY_CONFLICT`.

```http
Idempotency-Key: flowcanvas:42:image:0
```

```json
{
  "kind": "image",
  "prompt": "A premium product shot",
  "media_ids": ["123456789012345"],
  "options": {
    "model": "NANO_BANANA_2",
    "aspect_ratio": "9:16",
    "output_count": 1
  }
}
```

`kind` is `image`, `video`, or `omni`. `media_ids` are Provider-owned reference IDs returned by `POST /v1/media`. Provider-specific normalization, account selection, project handling, capacity and retry policy remain inside FlowProviderAPI.

Response:

```json
{
  "task_id": "job_...",
  "status": "queued",
  "outputs": [],
  "error": null
}
```

Poll `GET /v1/status/{task_id}` while `status` is `queued` or `running`. Stop on `succeeded`, `failed`, or `canceled`.

List caller-owned generation statuses with `GET /v1/status`; request cooperative cancellation with `POST /v1/status/{task_id}/cancel`.

If the caller cannot tell whether a prior `POST /v1/generations` completed, retry that POST with the same `Idempotency-Key`. Do not generate a new key for the same logical submission.

## Reference media

Upload application-owned reference bytes with `POST /v1/media`. `media_id` is a **15-digit JSON string**, not a number. Within one API client, repeated uploads of identical ready content and media type are content-deduplicated by SHA-256 and return the existing media object.

Generated output URLs are upstream-owned and may expire. An application that needs durable media must copy successful output bytes into its own storage.

## Ownership boundary

FlowProviderAPI owns provider-specific normalization, account scheduling, Google Flow projects/media mapping, leases, polling, provider errors, connector credentials, and durable remote-submission idempotency. The calling application owns its own jobs/workflows, user authorization, stable logical idempotency keys, and durable result storage.
