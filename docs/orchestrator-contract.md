# Orchestrator contract

FlowProviderAPI exposes a small server-to-server generation boundary for application backends such as FlowCanvas.

## Unified generation

`POST /v1/generations`

Each POST is an independent generation submission. The API does not expose or honor `Idempotency-Key`; callers should store the returned `task_id` immediately and poll that task's status instead of resubmitting while it is still in progress.

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

## Reference media

Upload application-owned reference bytes with `POST /v1/media`. `media_id` is a **15-digit JSON string**, not a number. Within one API client, repeated uploads of identical ready content and media type are content-deduplicated by SHA-256 and return the existing media object.

Generated output URLs are upstream-owned and may expire. An application that needs durable media must copy successful output bytes into its own storage.

## Ownership boundary

FlowProviderAPI owns provider-specific normalization, account scheduling, Google Flow projects/media mapping, leases, polling, provider errors, and connector credentials. The calling application owns its own jobs/workflows, user authorization, submission deduplication if desired, and durable result storage.
