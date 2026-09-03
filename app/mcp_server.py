from __future__ import annotations

import argparse
import asyncio
import base64
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.api.schemas import MAX_BASE64_TOTAL_CHARS

ImageModel = Literal["pro", "v2"]
ImageAspect = Literal["1:1", "16:9", "9:16"]
VideoAspect = Literal["16:9", "9:16"]
CharacterEntity = Literal[
    "character", "location", "creature", "visual_asset", "generic_troop", "faction",
]
VideoQuality = Literal["lite", "fast", "quality", "lite_relaxed", "fast_relaxed"]
VideoType = Literal[
    "frames_to_video",
    "reference_to_video",
    "frames",
    "ingredients",
    "references",
    "start_to_video",
    "image_to_video",
    "omni",
    "r2v",
    "i2v",
]

IMAGE_MIME_TYPES = {
    ".avif": "image/avif",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
FORWARDED_RESPONSE_HEADERS = {
    "x-flow-media-cache-hits",
    "x-flow-mock",
    "x-flow-project-id",
    "x-flow-upstream-status",
    "x-flow-video-urls",
    "x-provider-routing-scope",
    "x-request-id",
}


class MCPSettings(BaseSettings):
    """Connection settings for the local MCP adapter."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    base_url: str = Field(
        default="http://localhost:8000",
        validation_alias=AliasChoices(
            "FLOW_PROVIDER_MCP_BASE_URL",
            "FLOW_PROVIDER_PUBLIC_BASE_URL",
        ),
    )
    timeout_seconds: float = Field(
        default=300,
        ge=1,
        le=1800,
        validation_alias="FLOW_PROVIDER_MCP_TIMEOUT_SECONDS",
    )
    allowed_roots: str = Field(
        default=".",
        validation_alias="FLOW_PROVIDER_MCP_ALLOWED_ROOTS",
    )

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MCP base URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("MCP base URL cannot contain credentials, a query, or a fragment")
        return value.rstrip("/")

    @field_validator("allowed_roots")
    @classmethod
    def validate_allowed_roots(cls, value: str) -> str:
        roots = [item.strip() for item in value.split(os.pathsep) if item.strip()]
        if not roots:
            raise ValueError("MCP allowed roots must contain at least one directory")
        for item in roots:
            try:
                root = Path(item).expanduser().resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ValueError(f"MCP allowed root does not exist: {item}") from exc
            if not root.is_dir():
                raise ValueError(f"MCP allowed root is not a directory: {item}")
        return value

    @property
    def allowed_root_paths(self) -> tuple[Path, ...]:
        return tuple(
            Path(item.strip()).expanduser().resolve(strict=True)
            for item in self.allowed_roots.split(os.pathsep)
            if item.strip()
        )


class FlowToolResult(BaseModel):
    """Structured result returned to the agent for a FlowProvider request."""

    status_code: int
    data: dict[str, Any]
    metadata: dict[str, str] = Field(default_factory=dict)


class FlowProviderClient:
    def __init__(
        self,
        settings: MCPSettings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or MCPSettings()
        self._client = httpx.AsyncClient(
            base_url=self.settings.base_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "FlowProviderMCP/1.0",
            },
            timeout=self.settings.timeout_seconds,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: Literal["GET", "POST", "PATCH", "DELETE"],
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        routing_scope: str | None = None,
    ) -> FlowToolResult:
        headers = {}
        if routing_scope:
            headers["X-Provider-Routing-Scope"] = routing_scope
        try:
            response = await self._client.request(
                method,
                path,
                json=body,
                params=params,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise ToolError(
                f"FlowProviderAPI timed out after {self.settings.timeout_seconds:g} seconds. "
                "The operation may still have been accepted; check status before retrying a paid video request."
            ) from exc
        except httpx.RequestError as exc:
            raise ToolError(f"Cannot reach FlowProviderAPI: {exc}") from exc

        if response.status_code == 204 and not response.content:
            decoded = {}
        else:
            try:
                decoded = response.json()
            except ValueError:
                decoded = {"raw": response.text[:4000]}
        data = decoded if isinstance(decoded, dict) else {"value": decoded}
        metadata = {
            name: value
            for name, value in response.headers.items()
            if name.lower() in FORWARDED_RESPONSE_HEADERS
        }

        if response.is_error:
            error = data.get("error") if isinstance(data.get("error"), dict) else {}
            code = error.get("code") or "UPSTREAM_ERROR"
            message = error.get("message") or response.reason_phrase
            request_id = error.get("request_id") or metadata.get("x-request-id")
            retryable = error.get("retryable") is True
            details = error.get("details") if isinstance(error.get("details"), list) else []
            detail_parts = []
            for item in details[:8]:
                if not isinstance(item, dict):
                    continue
                field = str(item.get("field") or "request")[:120]
                detail_code = str(item.get("code") or "INVALID_VALUE")[:120]
                detail_message = str(item.get("message") or "Invalid value.")[:300]
                detail_parts.append(f"{field} {detail_code}: {detail_message}")
            detail_suffix = f" details=[{' | '.join(detail_parts)}]" if detail_parts else ""
            suffix = f" request_id={request_id}" if request_id else ""
            raise ToolError(
                f"FlowProviderAPI HTTP {response.status_code} {code}: {message}; "
                f"retryable={str(retryable).lower()}.{detail_suffix}{suffix}"
            )

        return FlowToolResult(
            status_code=response.status_code,
            data=data,
            metadata=metadata,
        )


def _encoded_length(byte_count: int) -> int:
    return 4 * ((byte_count + 2) // 3)


def _read_limited(path: Path, max_bytes: int) -> bytes:
    with path.open("rb") as stream:
        return stream.read(max_bytes + 1)


async def _encode_image(
    path_value: str,
    allowed_roots: tuple[Path, ...],
    *,
    max_encoded_chars: int = MAX_BASE64_TOTAL_CHARS,
) -> dict[str, str]:
    path = Path(path_value).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ToolError(f"Image file does not exist or cannot be resolved: {path_value}") from exc
    if not any(resolved.is_relative_to(root) for root in allowed_roots):
        raise ToolError("Image path is outside FLOW_PROVIDER_MCP_ALLOWED_ROOTS")
    if not resolved.is_file():
        raise ToolError(f"Image path is not a regular file: {resolved}")
    mime_type = IMAGE_MIME_TYPES.get(resolved.suffix.lower())
    if not mime_type:
        supported = ", ".join(sorted(IMAGE_MIME_TYPES))
        raise ToolError(f"Unsupported image extension {resolved.suffix!r}; supported: {supported}")
    try:
        file_size = resolved.stat().st_size
        if _encoded_length(file_size) > max_encoded_chars:
            raise ToolError("Image exceeds the remaining FlowProvider Base64 request limit")
        max_raw_bytes = (max_encoded_chars // 4) * 3
        content = await asyncio.to_thread(_read_limited, resolved, max_raw_bytes)
    except OSError as exc:
        raise ToolError(f"Cannot read image file: {resolved}") from exc
    if len(content) > max_raw_bytes:
        raise ToolError("Image exceeds the remaining FlowProvider Base64 request limit")
    encoded = base64.b64encode(content).decode("ascii")
    if len(encoded) > max_encoded_chars:
        raise ToolError("Image exceeds the remaining FlowProvider Base64 request limit")
    return {
        "image_base64": encoded,
        "mime_type": mime_type,
        "file_name": resolved.name,
    }


async def _encode_images(
    paths: list[str],
    allowed_roots: tuple[Path, ...],
) -> list[dict[str, str]]:
    encoded = []
    remaining = MAX_BASE64_TOTAL_CHARS
    for path in paths:
        image = await _encode_image(path, allowed_roots, max_encoded_chars=remaining)
        encoded.append(image)
        remaining -= len(image["image_base64"])
    return encoded


def build_mcp_server(client: FlowProviderClient | None = None) -> MCPServer:
    provider = client or FlowProviderClient()

    @asynccontextmanager
    async def lifespan(_server: MCPServer):
        try:
            yield provider
        finally:
            await provider.close()

    server = MCPServer(
        "flow-provider",
        title="FlowProvider for AI agents",
        description="Generate images and videos through authenticated Google Flow browser accounts.",
        version="1.0.0",
        instructions=(
            "Use flow_generate_image for image creation and flow_generate_video for video creation. "
            "Always omit project_id to let the Provider manage accounts and projects automatically. "
            "Always prefer passing local image files via image_paths instead of media_id; the system automatically "
            "hashes images with SHA-256 for 0ms deduplication caching and dynamically load-balances across all "
            "available Google accounts. For image-to-video or reference-to-video, pass the local image path or "
            "downloaded image path directly in image_paths. Image and video generation both return Provider job ids; "
            "read them with flow_get_job_status until status is complete or failed. Never create a second paid video "
            "while a job is queued or running. For Character workflows, create a Character, then call the dedicated "
            "Character image/video tool; those tools snapshot references in the DB. Generated URLs can expire, "
            "so download completed outputs promptly."
        ),
        lifespan=lifespan,
    )

    read_only = ToolAnnotations(
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=True,
    )
    mutating = ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    )
    paid_mutating = ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=True,
    )

    @server.tool(title="Check Flow readiness", annotations=read_only)
    async def flow_check_health() -> FlowToolResult:
        """Check whether FlowProviderAPI and at least one browser account are ready."""

        return await provider.request("GET", "/health/ready")

    @server.tool(title="Upload an image to Flow", annotations=mutating)
    async def flow_upload_image(
        image_path: str,
        project_id: str | None = None,
        routing_scope: str | None = None,
    ) -> FlowToolResult:
        """Upload one local image and return its Flow media ID for later image or video generation."""

        image = await _encode_image(image_path, provider.settings.allowed_root_paths)
        body: dict[str, Any] = {**image, "project_id": project_id}
        return await provider.request(
            "POST",
            "/v1/media",
            body=body,
            routing_scope=routing_scope,
        )

    @server.tool(title="Generate images with Flow", annotations=mutating)
    async def flow_generate_image(
        prompt: Annotated[str, Field(min_length=1, max_length=12000)],
        model: ImageModel = "pro",
        aspect_ratio: ImageAspect = "9:16",
        variant_count: Annotated[int, Field(ge=1, le=4)] = 1,
        image_paths: list[str] | None = None,
        reference_media_ids: list[str] | None = None,
        project_id: str | None = None,
        routing_scope: str | None = None,
    ) -> FlowToolResult:
        """Queue image generation and return a Provider job id for status checks. Prefer passing local file paths via image_paths for automatic SHA-256 caching and multi-account balancing."""

        paths = image_paths or []
        media_ids = reference_media_ids or []
        if len(paths) + len(media_ids) > 8:
            raise ToolError("Use at most 8 reference images and media IDs in total")
        body = {
            "project_id": project_id,
            "prompt": prompt,
            "model": model,
            "aspect_ratio": aspect_ratio,
            "reference_media_ids": media_ids,
            "input_images": await _encode_images(paths, provider.settings.allowed_root_paths),
            "variant_count": variant_count,
        }
        return await provider.request(
            "POST",
            "/v1/images/generations",
            body=body,
            routing_scope=routing_scope,
        )

    @server.tool(title="Create a Character", annotations=mutating)
    async def flow_create_character(
        name: Annotated[str, Field(min_length=1, max_length=200)],
        entity_type: CharacterEntity = "character",
        description: str | None = None,
        image_prompt: str | None = None,
        voice_description: str | None = None,
        image_model: ImageModel = "pro",
        aspect_ratio: ImageAspect | None = None,
        reference_media_ids: list[str] | None = None,
    ) -> FlowToolResult:
        """Create a reusable Character catalog entry from up to three uploaded image media IDs."""
        media_ids = reference_media_ids or []
        if len(media_ids) > 3:
            raise ToolError("A Character accepts at most 3 reference media IDs")
        return await provider.request(
            "POST", "/v1/characters", body={
                "name": name, "entity_type": entity_type, "description": description,
                "image_prompt": image_prompt, "voice_description": voice_description,
                "image_model": image_model, "aspect_ratio": aspect_ratio,
                "reference_media_ids": media_ids,
            },
        )

    @server.tool(title="List Characters", annotations=read_only)
    async def flow_list_characters(
        entity_type: CharacterEntity | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> FlowToolResult:
        """List active reusable Character catalog entries."""
        return await provider.request(
            "GET", "/v1/characters",
            params={"entity_type": entity_type, "limit": limit, "offset": offset},
        )

    @server.tool(title="Get Character", annotations=read_only)
    async def flow_get_character(character_id: str) -> FlowToolResult:
        """Get one reusable Character catalog entry."""
        return await provider.request("GET", f"/v1/characters/{character_id}")

    @server.tool(title="Update Character", annotations=mutating)
    async def flow_update_character(
        character_id: str,
        name: str | None = None,
        entity_type: CharacterEntity | None = None,
        description: str | None = None,
        image_prompt: str | None = None,
        voice_description: str | None = None,
        image_model: ImageModel | None = None,
        aspect_ratio: ImageAspect | None = None,
        reference_media_ids: list[str] | None = None,
    ) -> FlowToolResult:
        """Update Character metadata or replace its 1-3 reference images."""
        if reference_media_ids is not None and len(reference_media_ids) > 3:
            raise ToolError("A Character accepts at most 3 reference media IDs")
        body = {
            key: value for key, value in {
                "name": name, "entity_type": entity_type, "description": description, "image_prompt": image_prompt,
                "voice_description": voice_description, "image_model": image_model,
                "aspect_ratio": aspect_ratio, "reference_media_ids": reference_media_ids,
            }.items() if value is not None
        }
        return await provider.request("PATCH", f"/v1/characters/{character_id}", body=body)

    @server.tool(title="Delete Character", annotations=mutating)
    async def flow_delete_character(character_id: str) -> FlowToolResult:
        """Soft-delete a Character catalog entry without deleting its job history."""
        return await provider.request("DELETE", f"/v1/characters/{character_id}")

    @server.tool(title="Generate an image with a Character", annotations=mutating)
    async def flow_generate_character_image(
        character_id: str,
        prompt: Annotated[str, Field(min_length=1, max_length=12000)],
        model: ImageModel | None = None,
        aspect_ratio: ImageAspect | None = None,
        variant_count: Annotated[int, Field(ge=1, le=4)] = 1,
        image_paths: list[str] | None = None,
        reference_media_ids: list[str] | None = None,
        project_id: str | None = None,
        routing_scope: str | None = None,
    ) -> FlowToolResult:
        """Queue image generation using Character references plus optional extra images."""
        paths = image_paths or []
        media_ids = reference_media_ids or []
        if len(paths) + len(media_ids) > 8:
            raise ToolError("Use at most 8 extra reference images and media IDs in total")
        return await provider.request(
            "POST", f"/v1/characters/{character_id}/images/generations", body={
                "prompt": prompt, "model": model, "aspect_ratio": aspect_ratio,
                "project_id": project_id,
                "variant_count": variant_count,
                "reference_media_ids": media_ids,
                "input_images": await _encode_images(paths, provider.settings.allowed_root_paths),
            },
            routing_scope=routing_scope,
        )

    @server.tool(title="Generate a video with a Character", annotations=paid_mutating)
    async def flow_generate_character_video(
        character_id: str,
        prompt: Annotated[str, Field(min_length=1, max_length=12000)],
        aspect_ratio: VideoAspect = "9:16",
        duration_seconds: Literal[4, 6, 8, 10] = 8,
        dialogue: bool = False,
        project_id: str | None = None,
        routing_scope: str | None = None,
    ) -> FlowToolResult:
        """Queue a paid R2V video using up to three reference images of one Character."""
        return await provider.request(
            "POST", f"/v1/characters/{character_id}/videos/generations", body={
                "prompt": prompt, "aspect_ratio": aspect_ratio,
                "duration_seconds": duration_seconds, "dialogue": dialogue,
                "project_id": project_id,
            },
            routing_scope=routing_scope,
        )

    @server.tool(title="Generate a video with Flow", annotations=paid_mutating)
    async def flow_generate_video(
        type: VideoType,
        prompt: Annotated[str, Field(min_length=1, max_length=12000)],
        project_id: str | None = None,
        aspect_ratio: VideoAspect | None = None,
        start_media_id: str | None = None,
        end_media_id: str | None = None,
        reference_media_ids: list[str] | None = None,
        image_paths: list[str] | None = None,
        quality: VideoQuality | None = None,
        duration_seconds: Literal[4, 6, 8, 10] = 8,
        routing_scope: str | None = None,
    ) -> FlowToolResult:
        """Start a video generation job (frames_to_video or reference_to_video) using Gemini Omni Flash. Prefer passing local file paths via image_paths for automatic SHA-256 caching and multi-account load balancing."""

        is_frames = type in {"frames_to_video", "frames", "start_to_video", "image_to_video", "i2v", "omni_i2v"}
        paths = image_paths or []
        encoded_images = await _encode_images(paths, provider.settings.allowed_root_paths) if paths else []

        if is_frames:
            if not start_media_id and not encoded_images:
                raise ToolError("start_media_id is required (or provide local image_paths)")
            if reference_media_ids:
                raise ToolError("reference_media_ids is only valid for reference_to_video")
            default_aspect = "16:9" if type == "image_to_video" else "9:16"
            body: dict[str, Any] = {
                "type": type,
                "project_id": project_id,
                "prompt": prompt,
                "aspect_ratio": aspect_ratio or default_aspect,
                "duration_seconds": duration_seconds,
            }
            if start_media_id:
                body["start_media_id"] = start_media_id
            if end_media_id:
                body["end_media_id"] = end_media_id
            if encoded_images:
                body["input_images"] = encoded_images
            if quality and type == "image_to_video":
                body["quality"] = quality
        else:
            media_ids = reference_media_ids or []
            if not media_ids and not encoded_images:
                raise ToolError("reference_media_ids is required (or provide local image_paths)")
            if len(media_ids) + len(encoded_images) > 8:
                raise ToolError("Use at most 8 reference media IDs and image files in total")
            if start_media_id or end_media_id:
                raise ToolError("start_media_id and end_media_id are only valid for frames_to_video")
            body = {
                "type": type,
                "project_id": project_id,
                "prompt": prompt,
                "reference_media_ids": media_ids,
                "input_images": encoded_images,
                "aspect_ratio": aspect_ratio or "9:16",
                "duration_seconds": duration_seconds,
            }
        return await provider.request(
            "POST",
            "/v1/videos/generations",
            body=body,
            routing_scope=routing_scope,
        )

    @server.tool(title="Check Flow video status", annotations=read_only)
    async def flow_get_video_status(
        job_ids: Annotated[list[str], Field(min_length=1, max_length=20)],
    ) -> FlowToolResult:
        """Read video job states from the Provider database. Queued or running is normal."""

        return await provider.request(
            "POST",
            "/v1/jobs/status",
            body={"job_ids": job_ids},
        )

    @server.tool(title="Check Flow job status", annotations=read_only)
    async def flow_get_job_status(
        job_ids: Annotated[list[str], Field(min_length=1, max_length=20)],
    ) -> FlowToolResult:
        """Read image or video job states from the Provider database only."""

        return await provider.request(
            "POST",
            "/v1/jobs/status",
            body={"job_ids": job_ids},
        )

    return server


mcp = build_mcp_server()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FlowProvider MCP server for local AI agents over stdio",
    )
    parser.parse_args()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
