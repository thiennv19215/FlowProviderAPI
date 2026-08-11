# Architecture

```text
Application backends
       │ REST /v1
       ▼
GenerationJob (PostgreSQL)
       │
Global Scheduler ── client concurrency / priority / credit reservations
       │
Provider Registry
       ├── Google Flow ── Account Pool ── Extension Gateway ── Chrome profiles
       └── future OpenAI/Kling/etc.
       │
Asset Service ── R2/local storage
```

The provider platform intentionally does not own FlowCanvas Boards, Nodes, users, campaigns, TikTok state, or other application business logic.

Google Flow connections are runtime WebSockets. Durable generation state is in PostgreSQL. Extension installation IDs are used as stable Provider account identities so a reconnect does not orphan persisted video polling state or workspace/project mappings.

## Worker lanes and capacity

One process starts multiple async worker lanes (`FLOW_PROVIDER_WORKER_CONCURRENCY`, default 8). Lanes claim different PostgreSQL jobs and can overlap long image dispatches. Provider account capacity remains the real dispatch limit: the global scheduler counts active jobs per connected account and will not exceed each account's advertised slot capacity. Active jobs also reserve estimated credits; Omni reservations are duration-aware.

V1 intentionally keeps workers and the extension gateway in one process. The live socket registry and request rate limiter are process-local. Do not scale this deployment horizontally until a broker/router and distributed rate limiter are introduced. PostgreSQL job claiming itself is multi-worker safe, but extension socket ownership is not a multi-gateway design yet.

## Recovery boundaries

Video and Omni dispatch persist provider operation IDs before polling. Extension reconnects retain the same stable account ID. Transient polling or output-storage failures resume the existing provider operation; terminal provider failures finish the job instead of polling forever. Output assets are persisted incrementally so partial storage recovery does not require redispatching generation.

Provider media downloads are allowlisted and streamed into Provider-owned storage rather than buffered fully in application memory.
