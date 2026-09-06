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

`JobWorker` atomically claims queued work. On startup/loop it reconciles abandoned dispatch leases and expires jobs by configured timeout. A paid dispatch whose outcome may be unknown is not blindly recreated.

Image generation is dispatched and completed in one worker execution. Video generation stores upstream poll identifiers and the worker performs durable polling until complete/failed/timeout.

## Multi-account scheduling

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

Production Compose contains API + Cloudflare Tunnel and does not publish host port 8000. The tunnel reaches `http://api:8000` inside the Compose network. Remote backends and host-local processes normally call the public HTTPS hostname with Bearer auth.

CI verifies Python tests, extension JavaScript/tests and deployment config. Production deployment smoke verifies OpenAPI, unauthenticated rejection, authenticated `/v1/jobs/status`, and absence of legacy `/admin`. A real signed-in Google Flow E2E remains the final release acceptance gate.
