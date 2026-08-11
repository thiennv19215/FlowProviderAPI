# Jobs

Public states are `queued`, `running`, `succeeded`, `failed`, and `canceled`. The `stage` field is diagnostic and clients should not build business logic around individual stage names.

Video/Omni dispatch persists provider operation IDs before polling. Polling retries never redispatch the generation, preventing duplicate videos after transient failures or worker restarts.

`POST /v1/jobs/{id}/cancel` cancels queued work immediately and marks running work for cooperative cancellation.
