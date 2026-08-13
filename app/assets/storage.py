from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


class LocalStorage:
    provider = "local"
    bucket = None

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        root = self.root.resolve()
        if root not in path.parents and path != root:
            raise ValueError("invalid storage key")
        return path

    async def healthcheck(self) -> bool:
        try:
            await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)
            return await asyncio.to_thread(self.root.is_dir)
        except Exception:
            return False

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def put_file(self, key: str, path: Path, content_type: str) -> None:
        import shutil

        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, path, dest)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            await asyncio.to_thread(path.unlink)
        except FileNotFoundError:
            pass

    async def read_bytes(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def stat(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not await asyncio.to_thread(path.exists):
            return None
        info = await asyncio.to_thread(path.stat)
        return {"size_bytes": info.st_size}

    async def exists(self, key: str) -> bool:
        return await self.stat(key) is not None


class R2Storage:
    """Cloudflare R2 object storage using the S3-compatible API."""

    provider = "r2"

    def __init__(self, settings):
        import boto3
        from botocore.config import Config

        self.bucket = settings.r2_bucket
        self.download_url_expires_seconds = settings.r2_download_url_expires_seconds
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name=settings.r2_region,
            config=Config(signature_version="s3v4"),
        )

    async def healthcheck(self) -> bool:
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self.bucket)
            return True
        except Exception:
            return False

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    async def put_file(self, key: str, path: Path, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.upload_file,
            str(path),
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=key)

    async def read_bytes(self, key: str) -> bytes:
        response = await asyncio.to_thread(
            self._client.get_object,
            Bucket=self.bucket,
            Key=key,
        )
        return await asyncio.to_thread(response["Body"].read)

    async def stat(self, key: str) -> dict[str, Any] | None:
        try:
            response = await asyncio.to_thread(
                self._client.head_object,
                Bucket=self.bucket,
                Key=key,
            )
        except Exception as exc:
            status_code = getattr(getattr(exc, "response", None), "get", lambda *_: {})
            if callable(status_code):
                metadata = status_code("ResponseMetadata", {})
                if isinstance(metadata, dict) and metadata.get("HTTPStatusCode") == 404:
                    return None
            response = getattr(exc, "response", None)
            if isinstance(response, dict):
                error = response.get("Error") or {}
                if error.get("Code") in {"404", "NoSuchKey", "NotFound"}:
                    return None
            raise
        return {
            "size_bytes": int(response.get("ContentLength") or 0),
            "content_type": response.get("ContentType"),
            "etag": str(response.get("ETag") or "").strip('"') or None,
            "checksum": response.get("ChecksumSHA256"),
        }

    async def exists(self, key: str) -> bool:
        return await self.stat(key) is not None

    def create_download_url(self, key: str, *, expires_seconds: int | None = None) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_seconds or self.download_url_expires_seconds,
        )


def build_storage(settings):
    if settings.storage_backend == "r2":
        return R2Storage(settings)
    return LocalStorage(settings.local_storage_path)
