# Quickstart

All generation calls are asynchronous. Authenticate with `Authorization: Bearer <API_KEY>`. Every generation submission creates a new task.

```bash
curl -X POST http://localhost:8000/v1/images/generations \
  -H 'Authorization: Bearer fpa_dev_change_me' \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"A cinematic white cat","model":"banana_pro","aspect_ratio":"9:16"}'
```

The API returns HTTP `202` with a `task_id`. Poll it:

```bash
curl -H 'Authorization: Bearer fpa_dev_change_me' http://localhost:8000/v1/tasks/job_xxx
```

When `status` becomes `succeeded`, `outputs` contains `asset_id`, media `type`, and the direct Google Flow `url`. `asset_id` can be reused in later reference-based generation calls; the calling backend can use `url` immediately without another FlowProvider download endpoint.

FlowProvider does not copy generated output bytes into its storage. Direct Provider URLs may expire or be revoked, so download successful outputs promptly if your application needs durable media.
