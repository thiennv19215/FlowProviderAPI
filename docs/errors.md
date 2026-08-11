# Errors

Errors have one envelope:

```json
{"error":{"code":"INVALID_API_KEY","message":"The supplied API key is invalid.","type":"authentication_error","param":null,"request_id":"req_...","retryable":false}}
```

Every HTTP response has `X-Request-Id`. Authenticated responses also expose rate-limit headers. Provider polling failures are retryable internally and do not cause a new generation dispatch.
