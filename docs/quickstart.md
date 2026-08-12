# Quickstart

See [the current integration guide](integration.md) for the complete production contract.

All generation calls are asynchronous. Authenticate with `Authorization: Bearer <API_KEY>`. Every generation submission creates a new task; `Idempotency-Key` is not used by V1.

```bash
curl -X POST https://api.shopcongngheso5.io.vn/v1/images/generations \
  -H 'Authorization: Bearer fpa_live_...' \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"A cinematic white cat","model":"banana_pro","aspect_ratio":"9:16"}'
```

The API returns HTTP `202` with a `task_id`. Poll its status:

```bash
curl -H 'Authorization: Bearer fpa_live_...' \
  https://api.shopcongngheso5.io.vn/v1/status/job_xxx
```

Poll while `status` is `queued` or `running`; stop on `succeeded`, `failed`, or `canceled`.

When `status` becomes `succeeded`, `outputs` contains a string `media_id`, `type`, and the direct Google Flow `url`. `media_id` can be reused in later reference-based generation calls.

FlowProvider does not guarantee durable generated output URLs. Direct Provider URLs may expire or be revoked, so download successful outputs promptly if your application needs durable media.
