# Generation status

Public generation states are `queued`, `running`, `done`, `failed`, and `canceled`. Internal worker stages are deliberately not exposed in the public response.

Use `GET /v1/status/{task_id}` to read one generation status and `GET /v1/status` to list caller-owned statuses. Filters are validated: `status` accepts only the public states above and `type` accepts `image`, `video`, or `omni`.

Video/Omni dispatch persists provider operation IDs before polling. Polling retries never redispatch the generation, preventing duplicate videos after transient failures, extension reconnects, output-registration errors, or worker restarts. Terminal provider failures finish the generation instead of being polled indefinitely.

`POST /v1/status/{task_id}/cancel` cancels queued work immediately and marks running work for cooperative cancellation. The current Google Flow adapter does not claim that an already-dispatched upstream generation was canceled or that provider credits were refunded.

Every generation POST is independent. `Idempotency-Key` is not part of the public V1 contract.
