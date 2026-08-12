# Quickstart

See [the current integration guide](integration.md) for the complete production contract.

All generation calls are asynchronous. Authenticate with `Authorization: Bearer <API_KEY>`. Every generation submission creates a new task.

```bash
curl -X POST https://api.shopcongngheso5.io.vn/v1/images/generations \
  -H 'Authorization: Bearer fpa_live_...' \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"A cinematic white cat","model":"banana_pro","aspect_ratio":"9:16"}'
```

The API returns HTTP `202` with a `task_id`. Poll it:

```bash
curl -H 'Authorization: Bearer fpa_live_...' https://api.shopcongngheso5.io.vn/v1/tasks/task_xxx
```

When `status` becomes `succeeded`, `outputs` contains media `id` (`media_*` for newly created media), `type`, and the direct Google Flow `url`. `id` can be reused in later reference-based generation calls; the calling backend can use `url` immediately without another FlowProvider download endpoint.

FlowProvider does not copy generated output bytes into its storage. Direct Provider URLs may expire or be revoked, so download successful outputs promptly if your application needs durable media.
