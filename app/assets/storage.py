from __future__ import annotations

import asyncio
from pathlib import Path

import boto3


class LocalStorage:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path=(self.root/key).resolve()
        if self.root.resolve() not in path.parents and path != self.root.resolve():
            raise ValueError("invalid storage key")
        return path

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        path=self._path(key); path.parent.mkdir(parents=True,exist_ok=True)
        await asyncio.to_thread(path.write_bytes,data)

    async def read_bytes(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).exists)

    def presign_get(self, key: str, ttl: int) -> str | None:
        return None

    def presign_put(self, key: str, content_type: str, ttl: int) -> str | None:
        return None


class R2Storage:
    def __init__(self, settings):
        self.bucket=settings.r2_bucket
        self.client=boto3.client("s3", endpoint_url=settings.r2_endpoint_url, aws_access_key_id=settings.r2_access_key_id, aws_secret_access_key=settings.r2_secret_access_key, region_name=settings.r2_region)

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self.client.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    async def read_bytes(self, key: str) -> bytes:
        response=await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(response["Body"].read)

    async def exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(self.client.head_object, Bucket=self.bucket, Key=key); return True
        except Exception: return False

    def presign_get(self, key: str, ttl: int) -> str:
        return self.client.generate_presigned_url("get_object", Params={"Bucket":self.bucket,"Key":key}, ExpiresIn=ttl)

    def presign_put(self, key: str, content_type: str, ttl: int) -> str:
        return self.client.generate_presigned_url("put_object", Params={"Bucket":self.bucket,"Key":key,"ContentType":content_type}, ExpiresIn=ttl, HttpMethod="PUT")


def build_storage(settings):
    return R2Storage(settings) if settings.storage_backend=="r2" else LocalStorage(settings.local_storage_path)
