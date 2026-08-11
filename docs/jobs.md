# Jobs

Public states are `queued`, `running`, `succeeded`, `failed`, and `canceled`. The `stage` field is diagnostic and clients should not build business logic around individual stage names.

Video/Omni dispatch persists provider operation IDs before polling. Polling retries never redispatch the generation, preventing duplicate videos after transient failures, extension reconnects, output-storage errors, or worker restarts. Terminal provider failures finish the job instead of being polled indefinitely.

`POST /v1/jobs/{id}/cancel` cancels queued work immediately and marks running work for cooperative cancellation. The current Google Flow adapter does not contain a verified upstream cancel-generation primitive, so canceling a Provider job does not claim that the already-dispatched Google generation was canceled or that provider credits were refunded.
