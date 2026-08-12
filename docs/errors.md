# Errors

Errors have one envelope:

```json
{
  "error": {
    "status_code": 422,
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": [
      {
        "field": "model",
        "code": "INVALID_CHOICE",
        "message": "Input should be 'banana_pro' or 'banana_2'"
      }
    ],
    "request_id": "req_...",
    "retryable": false
  }
}
```

Use `error.code` and `details[].code` for application logic; messages are intended for logs and people. `details` is always an array and contains every validation issue, with dot-separated fields for nested input. Malformed JSON returns `400 INVALID_JSON`; valid JSON with invalid fields returns `422 VALIDATION_ERROR`. Authentication, authorization, missing endpoints/resources, conflicts, rate limits, and unexpected failures use the same envelope.

Every HTTP response has `X-Request-Id`, matching `error.request_id` on an error response. Send an existing value in `X-Request-Id` to correlate calls across services. Authentication errors include `WWW-Authenticate: Bearer`. Rate-limit errors include `Retry-After`; callers should retry only when `retryable` is `true`.

Asynchronous task failures expose the same error object inside the task response. `error.status_code`, `error.code`, and `error.message` preserve the upstream Flow HTTP status and error fields (for example `429 RESOURCE_EXHAUSTED` or `403 PERMISSION_DENIED`). The task lookup itself still returns HTTP `200` because the lookup succeeded; inspect `status: failed` and the nested error. Provider polling failures are retried internally without creating a new generation dispatch. Unexpected application exceptions return `INTERNAL_ERROR` without exposing exception details.
