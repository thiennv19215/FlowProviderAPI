# Idempotency

Send a stable `Idempotency-Key` on generation POSTs. `(client_id, idempotency_key)` is unique, so a client retry after a network timeout returns the original job instead of creating another provider generation.
