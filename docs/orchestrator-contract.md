# Orchestrator contract

FlowProviderAPI exposes a small server-to-server generation boundary for application backends such as FlowCanvas.

## Unified generation

`POST /v1/generations`

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

`kind` is `image`, `video`, or `omni`. `media_ids` are Provider-owned reference IDs returned by `POST /v1/media`. `options` are normalized and validated inside FlowProviderAPI; application orchestrators do not need to know Google Flow account, project, scheduling, capacity, or retry details.

The response is the same asynchronous task shape as the existing generation endpoints:

```json
{
  "task_id": "job_...",
  "status": "queued",
  "outputs": [],
  "error": null
}
```

Poll `GET /v1/tasks/{task_id}` until the task is terminal. Do not resubmit a task merely because it remains queued; FlowProviderAPI owns dispatch/capacity policy.

## Reference media

Upload application-owned reference bytes with `POST /v1/media`. `media_id` is a **15-digit JSON string**, not a number. Within one API client, repeated uploads of identical ready content and media type are content-deduplicated by SHA-256 and return the existing media object.

Generated output URLs are upstream-owned and may expire. An application that needs durable media must copy successful output bytes into its own storage.

## Ownership boundary

FlowProviderAPI owns provider-specific normalization, account scheduling, Google Flow projects/media mapping, leases, polling, provider errors, and connector credentials. The calling application owns its own jobs/workflows, user authorization, and durable result storage.
