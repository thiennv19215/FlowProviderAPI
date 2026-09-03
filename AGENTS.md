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

## MCP configuration

- Transport is local `stdio` only.
- Configure the REST backend through `FLOW_PROVIDER_MCP_BASE_URL`.
- Configure readable image directories through `FLOW_PROVIDER_MCP_ALLOWED_ROOTS`.
- Optional timeout: `FLOW_PROVIDER_MCP_TIMEOUT_SECONDS` (1-1800 seconds).
- There is no `FLOW_PROVIDER_MCP_API_KEY` setting.
- Never give an agent `FLOW_PROVIDER_EXTENSION_API_KEY`; it is only for Provider-to-extension authentication.

## Tool workflow

1. Call `flow_check_health` before a generation workflow when readiness is unknown.
2. Prefer managed projects by omitting `project_id` unless the caller needs an explicit project.
3. For local image inputs, use paths inside `FLOW_PROVIDER_MCP_ALLOWED_ROOTS`.
4. Preserve `metadata.x-flow-project-id` as `project_id` and `metadata.x-provider-routing-scope` as `routing_scope` when continuing an account/project-bound media workflow.
5. For frames_to_video (start image to video), pass the source media ID as `start_media_id` (and optional `end_media_id`).
6. For reference_to_video (Omni video), pass 1-8 IDs as `reference_media_ids` (or inline Base64 in `input_images`).
7. After `flow_generate_image` or `flow_generate_video`, preserve every Provider identifier returned in `jobs[].id` and its `type` (`image` or `video`) and `generation_type` (`image`, `frames_to_video`, or `reference_to_video`).
8. Read the same identifiers with `flow_get_job_status`. `queued` and `running` are normal; status reads only the Provider database.
9. Never start a second paid video merely because the first is queued/running or a timeout left acceptance uncertain.
10. Treat `jobs[].status == "failed"` as terminal. Download successful signed URLs promptly because they expire.

## MCP value constraints

- Image model: `pro` or `v2`.
- Image aspect ratio: `1:1`, `16:9`, or `9:16` (default `9:16`).
- Image variants: 1-4.
- Image references: at most 8 combined local paths and media IDs.
- Video type: `frames_to_video` (aliases: `frames`, `start_to_video`, `image_to_video`, `i2v`) or `reference_to_video` (aliases: `ingredients`, `references`, `omni`, `r2v`).
- Video aspect ratio default: `9:16` (portrait) or `16:9` (landscape).
- Video duration: 4, 6, 8, or 10 seconds (default 8).
- Job status accepts 1-20 Provider job IDs.

## Response handling

Image and video creation return normalized `jobs[]`; each job has `id`, `type`, `status`, `media`, and `error`. Public statuses are `queued`, `running`, `complete`, and `failed`. Job status reads durable Provider state by `job_ids`; only the worker polls upstream video operations, while image jobs finish in one worker call.

For full contracts, read `docs/mcp-agent.vi.md` first and use `docs/integration-guide.vi.md` for REST response details and error handling.
