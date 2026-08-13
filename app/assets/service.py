from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import MediaAsset, ProjectMediaMapping
from app.ids import new_id, new_numeric_id
from app.providers.base import ProviderMedia

PROVIDER_MEDIA_HOSTS = {
    "labs.google",
    "flow.google",
    "flow-content.google",
    "storage.googleapis.com",
    "googleusercontent.com",
}


class AssetService:
    def __init__(self, storage, settings):
        self.storage = storage
        self.settings = settings

    @staticmethod
    def storage_key(client_id: str, asset_id: str, filename: str | None, mime_type: str) -> str:
        suffix = PurePosixPath(filename or "").suffix
        if not suffix:
            suffix = mimetypes.guess_extension(mime_type) or ""
        return f"clients/{client_id}/{asset_id}{suffix[:12]}"

    @staticmethod
    def _new_available_id(db) -> str:
        for _ in range(8):
            candidate = new_numeric_id()
            lookup = getattr(db, "get", None)
            if lookup is None or lookup(MediaAsset, candidate) is None:
                return candidate
        raise RuntimeError("media_id_allocation_failed")

    def _provider_url_allowed(self, value: str) -> bool:
        try:
            parsed = urlparse(value)
            host = (parsed.hostname or "").lower()
            if parsed.scheme != "https":
                return self.settings.env in {"development", "test"} and host in {
                    "127.0.0.1",
                    "localhost",
                }
            return any(
                host == allowed or host.endswith("." + allowed)
                for allowed in PROVIDER_MEDIA_HOSTS
            )
        except Exception:
            return False

    def create_pending(
        self,
        db,
        *,
        client_id: str,
        filename: str,
        mime_type: str,
        asset_type: str,
        size_bytes: int | None = None,
    ) -> MediaAsset:
        for _ in range(3):
            aid = self._new_available_id(db)
            key = self.storage_key(client_id, aid, filename, mime_type)
            asset = MediaAsset(
                id=aid,
                client_id=client_id,
                status="pending",
                type=asset_type,
                storage_key=key,
                filename=filename,
                mime_type=mime_type,
                size_bytes=size_bytes,
            )
            db.add(asset)
            try:
                db.commit()
                db.refresh(asset)
                return asset
            except IntegrityError:
                db.rollback()
        raise RuntimeError("media_id_allocation_failed")

    async def _reject_pending_object(self, asset: MediaAsset, code: str):
        try:
            if asset.storage_key:
                await self.storage.delete(asset.storage_key)
        except Exception:
            pass
        raise ValueError(code)

    async def complete_pending(self, db, asset: MediaAsset) -> MediaAsset:
        meta = await self.storage.stat(asset.storage_key)
        if not meta:
            raise FileNotFoundError("uploaded_object_not_found")
        size = meta.get("size_bytes")
        if isinstance(size, int) and size > self.settings.max_upload_bytes:
            await self._reject_pending_object(asset, "uploaded_object_too_large")
        if asset.size_bytes is not None and isinstance(size, int) and size != asset.size_bytes:
            await self._reject_pending_object(asset, "uploaded_size_mismatch")
        content_type = meta.get("content_type")
        if (
            isinstance(content_type, str)
            and content_type
            and content_type.split(";", 1)[0].strip().lower()
            != asset.mime_type.split(";", 1)[0].strip().lower()
        ):
            await self._reject_pending_object(asset, "uploaded_content_type_mismatch")
        if isinstance(size, int):
            asset.size_bytes = size
        asset.status = "ready"
        db.commit()
        db.refresh(asset)
        return asset

    async def write_upload(self, db, asset: MediaAsset, data: bytes) -> MediaAsset:
        await self.storage.put_bytes(asset.storage_key, data, asset.mime_type)
        try:
            return await self.complete_pending(db, asset)
        except Exception:
            db.rollback()
            try:
                await self.storage.delete(asset.storage_key)
            except Exception:
                pass
            raise

    async def write_upload_file(
        self,
        db,
        asset: MediaAsset,
        path: Path,
        size_bytes: int,
    ) -> MediaAsset:
        if size_bytes > self.settings.max_upload_bytes:
            raise ValueError("uploaded_object_too_large")
        await self.storage.put_file(asset.storage_key, path, asset.mime_type)
        try:
            asset.size_bytes = size_bytes
            asset.status = "ready"
            db.commit()
            db.refresh(asset)
            return asset
        except Exception:
            db.rollback()
            try:
                await self.storage.delete(asset.storage_key)
            except Exception:
                pass
            raise

    async def _external_to_temp_file(
        self,
        url: str,
        limit: int,
    ) -> tuple[Path, int, str, str | None]:
        if not self._provider_url_allowed(url):
            raise ValueError("external_asset_url_not_allowed")
        current_url = url
        tmp_path: Path | None = None
        digest = hashlib.sha256()
        size = 0
        content_type: str | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="flow-provider-output-", delete=False) as tmp:
                tmp_path = Path(tmp.name)
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(120, connect=20),
                    follow_redirects=False,
                ) as client:
                    for _ in range(6):
                        async with client.stream("GET", current_url) as response:
                            if response.is_redirect:
                                location = response.headers.get("location")
                                if not location:
                                    raise ValueError("external_asset_redirect_missing_location")
                                next_url = urljoin(current_url, location)
                                if not self._provider_url_allowed(next_url):
                                    raise ValueError("external_asset_redirect_not_allowed")
                                current_url = next_url
                                continue
                            response.raise_for_status()
                            declared = response.headers.get("content-length")
                            if declared:
                                try:
                                    if int(declared) > limit:
                                        raise ValueError("external_asset_too_large")
                                except ValueError as exc:
                                    if str(exc) == "external_asset_too_large":
                                        raise
                            raw_content_type = response.headers.get("content-type")
                            if raw_content_type:
                                content_type = raw_content_type.split(";", 1)[0].strip().lower()
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                if not chunk:
                                    continue
                                size += len(chunk)
                                if size > limit:
                                    raise ValueError("external_asset_too_large")
                                digest.update(chunk)
                                tmp.write(chunk)
                            tmp.flush()
                            return tmp_path, size, digest.hexdigest(), content_type
                    raise ValueError("external_asset_too_many_redirects")
        except Exception:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except FileNotFoundError:
                    pass
            raise

    async def ingest_provider_media(
        self,
        db,
        *,
        client_id: str,
        job_id: str,
        provider: str,
        media: ProviderMedia,
        asset_type: str,
        provider_project_id: str | None = None,
    ) -> MediaAsset:
        mime = media.mime_type or ("video/mp4" if asset_type == "video" else "image/png")
        aid = self._new_available_id(db)
        key = self.storage_key(client_id, aid, None, mime)
        size = None
        checksum_value = None
        stored = False
        tmp_path: Path | None = None
        limit = getattr(self.settings, "max_provider_output_bytes", 1024 * 1024 * 1024)
        try:
            if media.url:
                if not self._provider_url_allowed(media.url):
                    raise ValueError("provider_output_url_not_allowed")
                if media.thumbnail_url and not self._provider_url_allowed(media.thumbnail_url):
                    raise ValueError("provider_output_thumbnail_url_not_allowed")
                tmp_path, size, checksum_value, downloaded_mime = await self._external_to_temp_file(
                    media.url,
                    limit,
                )
                if downloaded_mime and downloaded_mime.startswith(("image/", "video/")):
                    mime = downloaded_mime
                    key = self.storage_key(client_id, aid, None, mime)
                await self.storage.put_file(key, tmp_path, mime)
                stored = True
            elif media.bytes_data is not None:
                data = media.bytes_data
                size = len(data)
                if size > limit:
                    raise ValueError("provider_output_too_large")
                checksum_value = hashlib.sha256(data).hexdigest()
                await self.storage.put_bytes(key, data, mime)
                stored = True
            else:
                raise ValueError("provider_output_has_no_content")

            asset = MediaAsset(
                id=aid,
                client_id=client_id,
                status="ready",
                type=asset_type,
                storage_key=key,
                external_url=None,
                thumbnail_url=media.thumbnail_url if asset_type == "video" else None,
                mime_type=mime,
                size_bytes=size,
                width=media.width,
                height=media.height,
                duration=media.duration,
                checksum_sha256=checksum_value,
                source_provider=provider,
                source_job_id=job_id,
            )
            db.add(asset)
            if media.media_id and provider_project_id:
                db.add(
                    ProjectMediaMapping(
                        id=new_id("map"),
                        asset_id=aid,
                        provider=provider,
                        provider_project_id=provider_project_id,
                        provider_media_id=media.media_id,
                    )
                )
            db.commit()
            db.refresh(asset)
            return asset
        except Exception:
            db.rollback()
            if stored:
                try:
                    await self.storage.delete(key)
                except Exception:
                    pass
            raise
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except FileNotFoundError:
                    pass

    async def _external_bytes(self, url: str, limit: int) -> bytes:
        path, size, _checksum, _content_type = await self._external_to_temp_file(url, limit)
        try:
            if size > limit:
                raise ValueError("external_asset_too_large")
            return await __import__("asyncio").to_thread(path.read_bytes)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    async def bytes_for_asset(self, asset: MediaAsset, *, max_bytes: int | None = None) -> bytes:
        if asset.storage_key:
            data = await self.storage.read_bytes(asset.storage_key)
            limit = max_bytes or self.settings.max_reference_in_memory_bytes
            if len(data) > limit:
                raise ValueError("external_asset_too_large")
            return data
        if asset.external_url:
            # Backward compatibility for rows created before Provider-owned
            # object storage. New media always receives a storage_key.
            limit = max_bytes or self.settings.max_reference_in_memory_bytes
            return await self._external_bytes(asset.external_url, limit)
        raise FileNotFoundError("asset_has_no_content")

    def content_url(self, asset: MediaAsset) -> str:
        if asset.storage_key:
            signer = getattr(self.storage, "create_download_url", None)
            if callable(signer):
                return signer(
                    asset.storage_key,
                    expires_seconds=self.settings.r2_download_url_expires_seconds,
                )
            return f"{self.settings.public_base_url.rstrip('/')}/media/{asset.id}"
        if asset.external_url:
            return asset.external_url
        raise FileNotFoundError("asset_has_no_content")

    @staticmethod
    def get_owned(db, asset_id: str, client_id: str) -> MediaAsset | None:
        return db.scalar(
            select(MediaAsset).where(
                MediaAsset.id == asset_id,
                MediaAsset.client_id == client_id,
            )
        )
