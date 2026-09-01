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
5. For image-to-video, pass the source media ID as `start_media_id`.
6. For Omni video, pass 1-8 IDs as `reference_media_ids`.
7. After `flow_generate_video`, extract every poll identifier from `operations[].operation.name` or `operations[].name`, then `workflows[].name`; if neither exists, use `media[].name`.
8. Poll the same identifiers with `flow_get_video_status`. Pending is normal.
9. Never start a second paid video merely because the first is pending or a timeout left acceptance uncertain.
10. Treat `operation.error` or media failure as terminal. Download successful signed URLs promptly because they expire.

## MCP value constraints

- Image model: `pro` or `v2`.
- Image aspect ratio: `1:1`, `16:9`, or `9:16` (default `9:16`).
- Image variants: 1-4.
- Image references: at most 8 combined local paths and media IDs.
- Video type: `image_to_video` or `omni`.
- Video aspect ratio defaults: `16:9` for image-to-video, `9:16` for Omni.
- Image-to-video quality: `lite`, `fast`, `quality`, `lite_relaxed`, or `fast_relaxed`.
- Omni duration: 4, 6, 8, or 10 seconds (default 8).
- Video status accepts 1-20 poll identifiers.

## Response handling

MCP success results contain `status_code`, upstream `data`, and selected headers under `metadata`. Do not assume generated images live under `images[]`; current upstream responses commonly use `media[]`. Do not assume video creation always returns `operations[]`; support `operations[]`, `workflows[]`, and the `media[]` fallback. Preserve upstream response fields rather than inventing aliases.

For full contracts, read `docs/mcp-agent.vi.md` first and use `docs/integration-guide.vi.md` for REST response details and error handling.
