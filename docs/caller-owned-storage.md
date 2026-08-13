# Caller-owned storage

## Gateway-only deployment

The FlowCanvas gateway is the only deployment mode. It does not contain a database
engine, worker, Provider asset storage, or R2 client.
FlowCanvas persists request/idempotency state and owns every durable object.
The Provider accepts scoped FlowCanvas URLs, uses its live Chrome extension to
call Google Flow, and uploads output directly to the supplied destination.

The hostname allowlist remains required as an outbound-request (SSRF) boundary;
it is not an R2 credential and no Provider Fernet key is needed in this mode.

FlowProviderAPI is an execution gateway. `MediaAsset`, `/v1/media`, and
Provider delivery routes no longer exist.

`caller_owned_output` is an explicit provider capability. Google Flow declares
it here; a future provider can opt in without inheriting Google account pools,
Chrome extensions, or Google project/media semantics.

Enable only after configuring:

- `FLOW_PROVIDER_CALLER_OWNED_ALLOWED_HOSTS` with exact FlowCanvas storage
  hostnames
- a stable `Idempotency-Key` on every generation request

The gateway has no durable job JSON. FlowCanvas keeps `asset_key`, checksum,
MIME, size and output index as logical identity, locks each logical submission,
and detects idempotency conflicts. The gateway derives the same response
`task_id` from the same key, but cannot cache or replay a completed response.

For each execution the Provider downloads references with bounded, allowlisted
requests, verifies checksums, uploads them into a temporary Google project,
polls video/Omni operations when necessary, and uploads outputs directly to
the supplied destinations.

The synchronous result returns `output_index`, MIME, size, checksum and
`uploaded`; it does not return a Provider `media_id`, Google URL, or signed URL.
FlowCanvas is responsible for durable status, retry decisions, storage stat
verification and final Asset creation.
