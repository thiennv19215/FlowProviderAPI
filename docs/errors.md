# Errors

Errors have one envelope:

```json
{"error":{"code":"INVALID_API_KEY","message":"The supplied API key is invalid.","type":"authentication_error","param":null,"request_id":"req_...","retryable":false}}
```

Every HTTP response has `X-Request-Id`. Authenticated responses also expose rate-limit headers. Provider polling failures are retried internally without creating a new generation dispatch. Unexpected application exceptions are converted to the same envelope with `code: INTERNAL_ERROR` and do not expose internal exception details to API clients.
