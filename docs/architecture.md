# Architecture

```text
Trusted backend / MCP adapter
        |
        | HTTPS /v1/* + Bearer business API key
        v
FlowProviderAPI (FastAPI)
        |
        +--> SQLite durable jobs / project-media routes / idempotency
        +--> content-addressed asset store
        +--> in-process JobWorker
                    |
                    v
             Chrome MV3 connector
                    |
                    v
                Google Flow
```

## Security boundary

Production `/v1/*` is server-to-server only and requires `FLOW_PROVIDER_BOOTSTRAP_API_KEY` as a Bearer token. The Chrome connector uses a separate `FLOW_PROVIDER_EXTENSION_API_KEY`. The two production credentials must differ.

Google cookies, bearer tokens and captcha/session state stay in the signed-in browser connector. SQLite does not store Google credentials.

## Public business API

The current stable business surface is:

- `POST /v1/media`
- `POST /v1/images/generations`
- `POST /v1/videos/generations`
- `POST /v1/jobs/status`
- Character CRUD/generation routes under `/v1/characters`

There is no public `/v1/projects` endpoint in the current contract. Managed projects are an internal Provider concern.

## Durable job lifecycle

Image/video creation persists a Provider job before asynchronous processing and returns `202` with `jobs[].id`.

```text
queued -> dispatching/running -> completed | failed
              |
              +-> public status is normalized to running
```

Clients see only:

```text
queued -> running -> complete | failed
```

`POST /v1/jobs/status` reads durable Provider state; it does not consume a browser extension slot.

Generation endpoints support `Idempotency-Key`. Repeating the same key with the same logical payload returns the existing job; reusing it for a different payload is rejected. Paid video callers should always use idempotency keys.

## Worker and restart recovery

HTTP routers and the worker share upstream routing/media/project operations through
`app/services/flow.py`; the worker does not import HTTP routers. HTTP response
normalization stays at the API boundary. `ProjectStore` is the single authority
for job timeouts; `RuntimeProjectStore` is only a compatibility alias.

`JobWorker` atomically claims queued work. On startup/loop it reconciles abandoned dispatch leases and expires jobs by configured timeout. A paid dispatch whose outcome may be unknown is not blindly recreated.

Image generation is dispatched and completed in one worker execution. Video generation stores upstream poll identifiers and the worker performs durable polling until complete/failed/timeout.

## Multi-account scheduling

The queue orders by creation time for new work and retry eligibility time for
deferred work (image preference only breaks timestamp ties).
Jobs blocked by route/capacity are released with a persisted `next_dispatch_at`,
so they cannot repeatedly occupy every slot in the next batch. Dispatch claims
remain atomic and guarded by an ownership token.

Ready Chrome connectors are filtered by health, capacity, cooldown and available credits. The runtime chooses an eligible connection and reserves its slot/estimated credit cost before dispatch.

Paid work invalidates cached balance after an attempt and triggers refresh. This prevents concurrent paid jobs from repeatedly spending the same observed balance.

If an unscoped job can safely move, the worker may fail over to another eligible Google account. Project/media ownership is preserved; references are never silently sent as foreign Google media IDs to another account.

## Managed project/media flow

Callers normally omit `project_id`. For the selected account the Provider:

1. reuses the persisted managed project when valid;
2. otherwise lists Google Flow projects and reuses the newest known project;
3. creates a `FlowProvider` project only when lookup confirms the account has no project.

Inline images are SHA-256 hashed. Source bytes used by durable jobs are stored in the content-addressed asset store before enqueueing so a restart does not lose required input. Account/project-specific Google media mappings are cached in SQLite.

When scheduling changes account/project, known images can be rehydrated by reading the durable source asset or downloading through the owning connector and uploading to the target project.

## Routing scope compatibility

`X-Provider-Routing-Scope` is an opaque compatibility token for workflows that explicitly reuse account/project-bound Google Flow resources. It is HMAC-signed with the private extension connector credential and identifies the connector installation + normalized Google account identity.

Managed generation with inline inputs usually does not need callers to persist a routing scope. When a scoped request is used, the Provider must not route it to an unrelated account if the owning route is unavailable.

Routing scopes are returned by current media/generation responses when a concrete route is known. They are **not** created through a `/v1/projects` API.

## Health

- `/health/live`: process liveness; used by Docker/deploy.
- `/health/ready`: store + at least one ready Google Flow connector; returns 503 otherwise.
- `/api/health`: connector/bootstrap-compatible aggregate health.

Health responses intentionally exclude account email, detailed credits and connector error details.

## Character references

Character source images are retained content-addressed under the configured asset store. Character generation snapshots references into durable jobs. Generated output does not replace the Character's reference catalog automatically.

## Deployment topology

Supported topology: **one API process, one replica, one local durable volume**.
Connector sockets, account slots and credit reservations are process-local.
SQLite job claims do not make multiple runtimes safe. Production lifespan holds
an OS file lock beside the database and refuses a second owner of that local
database; Docker explicitly uses `--workers 1`. This is not a distributed lock:
replicas with separate volumes or network filesystems are unsupported.

Production Compose contains API + Cloudflare Tunnel and does not publish host port 8000. The tunnel reaches `http://api:8000` inside the Compose network. Remote backends and host-local processes normally call the public HTTPS hostname with Bearer auth.

CI verifies Python tests, extension JavaScript/tests and deployment config. Production deployment smoke verifies OpenAPI, unauthenticated rejection, authenticated `/v1/jobs/status`, and absence of legacy `/admin`. A real signed-in Google Flow E2E remains the final release acceptance gate.

## Retention and maintenance

The enabled worker performs maintenance every `FLOW_PROVIDER_MAINTENANCE_INTERVAL_SECONDS`
(default 3600), off the event-loop thread. Each sweep mutates at most
`FLOW_PROVIDER_MAINTENANCE_BATCH_SIZE` records per category (default 500), rotating
through asset batches and logging counts. Reference checks still scan live
Character/job references; this is not a hard CPU-time bound for very large catalogs.
Referenced source assets remain protected, and orphan assets use the existing
`FLOW_PROVIDER_ASSET_RETENTION_DAYS` grace period. Maintenance does not run when
the worker is disabled.

Runtime image ingestion uses `ProjectStore.persist_asset` to write and register
bytes under the same lock as GC. A sweep resolves active-job media references
before deleting mappings and skips referenced mappings even when their cache age
has expired. Candidate batches rotate past protected mappings.

Historical job deletion is **off by default** (`FLOW_PROVIDER_JOB_RETENTION_DAYS=0`).
Setting a positive number of days opts into deletion of old terminal jobs without
an idempotency key and with a known outcome. Their IDs will no longer resolve.
Keyed jobs and `outcome_unknown` jobs are retained, including original request
payloads, so cleanup never allows an old paid request to be silently recreated.
These safety records can grow and must not be manually purged without reconciliation.
