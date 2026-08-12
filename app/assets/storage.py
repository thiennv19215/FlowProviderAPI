from __future__ import annotations

import asyncio
from pathlib import Path


class LocalStorage:
    def __init__(self, root: Path):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)

    def _path(self,key:str)->Path:
        path=(self.root/key).resolve()
        if self.root.resolve() not in path.parents and path!=self.root.resolve():raise ValueError("invalid storage key")
        return path

    async def healthcheck(self)->bool:
        try:
            await asyncio.to_thread(self.root.mkdir,parents=True,exist_ok=True)
            return await asyncio.to_thread(self.root.is_dir)
        except Exception:return False

    async def put_bytes(self,key:str,data:bytes,content_type:str)->None:
        path=self._path(key);path.parent.mkdir(parents=True,exist_ok=True);await asyncio.to_thread(path.write_bytes,data)

    async def put_file(self,key:str,path:Path,content_type:str)->None:
        import shutil
        dest=self._path(key);dest.parent.mkdir(parents=True,exist_ok=True);await asyncio.to_thread(shutil.copyfile,path,dest)

    async def delete(self,key:str)->None:
        path=self._path(key)
        try:await asyncio.to_thread(path.unlink)
        except FileNotFoundError:pass

    async def read_bytes(self,key:str)->bytes:return await asyncio.to_thread(self._path(key).read_bytes)

    async def stat(self,key:str)->dict|None:
        path=self._path(key)
        if not await asyncio.to_thread(path.exists):return None
        info=await asyncio.to_thread(path.stat);return {"size_bytes":info.st_size}

    async def exists(self,key:str)->bool:return await self.stat(key) is not None


def build_storage(settings):return LocalStorage(settings.local_storage_path)
