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
