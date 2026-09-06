# FlowProviderAPI agent instructions

## Scope

This repository provides a FastAPI gateway, a Chrome MV3 connector, and a local `stdio` MCP adapter for Google Flow image and video operations. Preserve the boundary:

```text
AI agent -> MCP adapter -> FlowProviderAPI -> signed-in Chrome extension -> Google Flow
```

The MCP adapter does not own Google cookies or tokens and does not expose a Streamable HTTP endpoint.

## Setup and verification

- Use Python 3.11 or newer.
- Install with `python -m pip install -e ".[dev]"`.
- The installed `mcp` package must satisfy `>=2,<3`.
- Run the API locally with `uvicorn app.main:app --reload`.
- Run the MCP adapter with `python -m app.mcp_server` or `flow-provider-mcp`.
- Run tests with `python -m pytest -q`.
- Keep generated media, `extension/dist/`, local `.env` files, and unrelated user changes out of documentation/code edits unless explicitly requested.

## Authentication boundaries

- Production `/v1/*` REST endpoints require `Authorization: Bearer <FLOW_PROVIDER_BOOTSTRAP_API_KEY>`.
- The Chrome connector uses a separate `FLOW_PROVIDER_EXTENSION_API_KEY`; never give this key to a backend client or AI agent.
- Production must use different business and extension keys.
- Health endpoints remain unauthenticated and expose aggregate readiness only.

## MCP configuration

- Transport is local `stdio` only.
- Configure the REST backend through `FLOW_PROVIDER_MCP_BASE_URL`.
- Configure readable image directories through `FLOW_PROVIDER_MCP_ALLOWED_ROOTS`.
- Optional timeout: `FLOW_PROVIDER_MCP_TIMEOUT_SECONDS` (1-1800 seconds).
- When the MCP adapter points to an authenticated production Provider, set `FLOW_PROVIDER_MCP_API_KEY`. If that variable is unset, the adapter can reuse `FLOW_PROVIDER_BOOTSTRAP_API_KEY` from the same environment.
- Local development remains compatible without an MCP API key when the local API runs outside production mode.

## Tool workflow

1. Call `flow_check_health` before a generation workflow when readiness is unknown.
2. Prefer managed projects by omitting `project_id` unless the caller needs an explicit project.
3. For local image inputs, use paths inside `FLOW_PROVIDER_MCP_ALLOWED_ROOTS`. Prefer image paths (or inline Base64 in REST `input_images`) over Flow media IDs to preserve SHA-256 deduplication and multi-account routing.
4. Preserve `metadata.x-flow-project-id` as `project_id` and `metadata.x-provider-routing-scope` as `routing_scope` when continuing an account/project-bound media workflow.
5. For frames-to-video, pass local `image_paths` in MCP or Base64 `input_images` in REST. Direct media IDs are intentionally rejected by the MCP video tool.
6. For reference-to-video, pass 1-8 local image paths through MCP or 1-8 inline images through REST.
7. After image/video creation, persist every `jobs[].id` immediately.
8. Read the same IDs with `flow_get_job_status`. `queued` and `running` are normal; status reads durable Provider state.
9. Never start a second paid video merely because the first is queued/running or a timeout left acceptance uncertain.
10. Treat `jobs[].status == "failed"` as terminal. Respect `retryable` and `outcome_unknown`; download successful signed URLs promptly because they expire.

## MCP value constraints

- Image model: `pro` or `v2`.
- Image aspect ratio: `1:1`, `16:9`, or `9:16` (default `9:16`).
- Image variants: 1-4.
- Image references: at most 8 combined local paths and media IDs.
- Video type: `frames_to_video` (aliases include `frames`, `start_to_video`, `image_to_video`, `i2v`) or `reference_to_video` (aliases include `ingredients`, `references`, `omni`, `r2v`).
- Video aspect ratio: `9:16` or `16:9`.
- Video duration: 4, 6, 8, or 10 seconds (default 8).
- Job status accepts 1-20 Provider job IDs.

## Response handling

Image and video creation return normalized `jobs[]`; each job has `id`, `type`, `status`, `media`, and `error`. Public statuses are `queued`, `running`, `complete`, and `failed`. Only the worker polls upstream video operations; image jobs finish in one worker dispatch.

For full contracts, read `docs/mcp-agent.vi.md` and `docs/integration-guide.vi.md`.
