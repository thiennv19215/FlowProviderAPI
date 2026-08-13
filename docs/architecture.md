# Architecture

```text
FlowCanvas durable job + idempotency + object storage
             | signed input/output URLs
             v
FlowProviderAPI stateless execution gateway
             |
             v
Chrome extension WebSocket -> signed-in Google Flow account
```

The process creates no SQL engine, worker, asset service, R2 client or durable Provider record. Only live Chrome connections and in-flight HTTP/RPC state exist in memory.

FlowCanvas selects a stable logical `Idempotency-Key`, stores request state before calling the gateway, and owns conflict detection and retry policy. The gateway returns a deterministic `gw_...` identifier but cannot replay an earlier response after a retry because it is stateless.

Each call selects a ready connection with an available slot, creates a temporary Google project, downloads checksum-verified references from allowlisted FlowCanvas hosts, calls Google Flow, polls video/Omni operations, and uploads bytes directly to the supplied FlowCanvas destination. The response contains metadata only.

Legacy database, worker, media and V1 routes have been removed from the repository and dependency graph.
