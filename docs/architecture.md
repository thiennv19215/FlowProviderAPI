# Architecture

```text
Application backends
       │ REST /v1
       ▼
GenerationJob (PostgreSQL)
       │
Global Scheduler ── client concurrency / priority
       │
Provider Registry
       ├── Google Flow ── Account Pool ── Extension Gateway ── Chrome profiles
       └── future OpenAI/Kling/etc.
       │
Asset Service ── R2/local storage
```

The provider platform intentionally does not own FlowCanvas Boards, Nodes, users, campaigns, TikTok state, or other application business logic.

Google Flow connections are runtime WebSockets. Durable generation state is in PostgreSQL. Multi-replica extension gateways will require a broker/router (Redis/NATS) later; V1 intentionally keeps one gateway owner while API/job contracts remain scale-safe.

## Worker lanes and capacity

One process starts multiple async worker lanes (`FLOW_PROVIDER_WORKER_CONCURRENCY`, default 8). Lanes claim different PostgreSQL jobs and can overlap long image dispatches. Provider account capacity remains the real dispatch limit: the global scheduler counts active jobs per connected account and will not exceed each account's advertised slot capacity. V1 intentionally keeps workers and the extension gateway in one process; a later multi-process gateway needs a broker/router for socket ownership.
