# Quickstart

All generation calls are asynchronous. Authenticate with `Authorization: Bearer <API_KEY>`. Every generation submission creates a new task.

```bash
curl -X POST http://localhost:8000/v1/images/generations \
  -H 'Authorization: Bearer fpa_dev_change_me' \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"A cinematic white cat","aspect_ratio":"9:16"}'
```

The API returns HTTP `202` with a `job_*` ID. Poll it:

```bash
curl -H 'Authorization: Bearer fpa_dev_change_me' http://localhost:8000/v1/jobs/job_xxx
```

When `status` becomes `succeeded`, `outputs` contains stable `asset_*` IDs and a content URL.
