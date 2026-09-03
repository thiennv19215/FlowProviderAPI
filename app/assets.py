from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import os
import uuid
from pathlib import Path

MAX_ASSET_BYTES = 25 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"}


class AssetStore:
    """Durable, content-addressed storage for caller-owned reference images."""

    def __init__(self, root: str):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._mime_types: dict[str, str] = {}

    @staticmethod
    def decode_image(image_base64: str, mime_type: str) -> bytes:
        normalized = mime_type.split(";", 1)[0].strip().lower()
        if normalized not in ALLOWED_IMAGE_TYPES:
            raise ValueError("Unsupported image MIME type")
        try:
            data = base64.b64decode(image_base64, validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise ValueError("Image data is not valid Base64") from exc
        if not data:
            raise ValueError("Image data is empty")
        if len(data) > MAX_ASSET_BYTES:
            raise ValueError("Image exceeds the 25 MiB asset limit")
        return data

    def put_base64(self, image_base64: str, mime_type: str) -> tuple[str, Path, int]:
        data = self.decode_image(image_base64, mime_type)
        return self.put_bytes(data, mime_type)

    def put_bytes(self, data: bytes, mime_type: str) -> tuple[str, Path, int]:
        normalized = mime_type.split(";", 1)[0].strip().lower()
        if normalized not in ALLOWED_IMAGE_TYPES:
            raise ValueError("Unsupported image MIME type")
        if not data or len(data) > MAX_ASSET_BYTES:
            raise ValueError("Image size is invalid")
        digest = hashlib.sha256(data).hexdigest()
        # The digest is the identity; do not create a second file when the
        # same bytes arrive with a different (but supported) MIME label.
        # Keep the content address as the filename. MIME type is persisted in
        # SQLite alongside the asset, so the bytes do not depend on a caller's
        # original extension.
        path = self.root / digest
        existing = self.path_for(digest)
        if existing is not None:
            self._mime_types.setdefault(digest, normalized)
            return digest, existing, len(data)
        if not path.exists():
            temporary = self.root / f".{digest}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            temporary.write_bytes(data)
            try:
                os.replace(temporary, path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        self._mime_types[digest] = normalized
        return digest, path, len(data)

    def path_for(self, digest: str) -> Path | None:
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest.lower())
        ):
            return None
        exact = self.root / digest.lower()
        if exact.is_file():
            return exact
        # Read assets written by an older build that used a MIME suffix.
        matches = list(self.root.glob(f"{digest.lower()}.*"))
        return matches[0] if matches and matches[0].is_file() else None

    def read(self, digest: str, mime_type: str | None = None) -> tuple[bytes, str] | None:
        path = self.path_for(digest)
        if path is None:
            return None
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest.lower():
            return None
        if mime_type:
            mime = mime_type
        else:
            mime = self._mime_types.get(digest.lower()) or mimetypes.guess_type(path.name)[0] or self._sniff_mime(data)
        return data, mime or "application/octet-stream"

    @staticmethod
    def _sniff_mime(data: bytes) -> str | None:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {b"avif", b"avis"}:
            return "image/avif"
        return None

    def delete(self, digest: str) -> bool:
        if not isinstance(digest, str):
            return False
        path = self.path_for(digest)
        if path is None:
            return False
        paths = {path}
        # Remove any legacy suffixed duplicate left by older asset-store
        # versions as well as the canonical extensionless file.
        paths.update(item for item in self.root.glob(f"{digest.lower()}.*") if item.is_file())
        for item in paths:
            item.unlink()
        self._mime_types.pop(digest.lower(), None)
        return True
