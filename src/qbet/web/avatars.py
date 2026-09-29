from __future__ import annotations

import hashlib
import html
import io
import json
import warnings
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from django.conf import settings


MAX_AVATAR_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_AVATAR_DIMENSION = 4096
AVATAR_SIZE = (512, 512)
_SUPPORTED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
_FALLBACK_COLORS = ("#155e75", "#166534", "#9a3412", "#6b21a8", "#1d4ed8", "#9f1239")


class AvatarValidationError(ValueError):
    pass


class AvatarStorageError(RuntimeError):
    pass


class AvatarStorage(Protocol):
    def upload(self, object_key: str, content: bytes) -> None: ...

    def download(self, object_key: str) -> bytes: ...

    def delete(self, object_key: str) -> None: ...


@dataclass(frozen=True)
class NormalizedAvatar:
    content: bytes


def normalize_avatar(upload) -> NormalizedAvatar:
    if upload.size > MAX_AVATAR_UPLOAD_BYTES:
        raise AvatarValidationError("Avatar must be 5 MB or smaller.")
    declared_type = (upload.content_type or "").lower()
    if declared_type not in _SUPPORTED_FORMATS.values():
        raise AvatarValidationError("Choose a JPEG, PNG, or WebP image.")

    content = upload.read(MAX_AVATAR_UPLOAD_BYTES + 1)
    if len(content) > MAX_AVATAR_UPLOAD_BYTES:
        raise AvatarValidationError("Avatar must be 5 MB or smaller.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                image_format = image.format
                width, height = image.size
                if image_format not in _SUPPORTED_FORMATS:
                    raise AvatarValidationError("Choose a JPEG, PNG, or WebP image.")
                if declared_type != _SUPPORTED_FORMATS[image_format]:
                    raise AvatarValidationError("The image content does not match its content type.")
                if (
                    width < 1
                    or height < 1
                    or width > MAX_AVATAR_DIMENSION
                    or height > MAX_AVATAR_DIMENSION
                ):
                    raise AvatarValidationError("Image dimensions must not exceed 4096 by 4096.")
                image.verify()
            with Image.open(io.BytesIO(content)) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                image.thumbnail(AVATAR_SIZE, Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=86, optimize=True)
    except AvatarValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise AvatarValidationError("Image dimensions are too large to process.") from None
    except (OSError, UnidentifiedImageError, ValueError):
        raise AvatarValidationError("The uploaded file is not a valid image.") from None
    return NormalizedAvatar(output.getvalue())


def avatar_fallback_svg(username: str, first_name: str = "", last_name: str = "") -> bytes:
    full_name = " ".join(part.strip() for part in (first_name, last_name) if part.strip())
    identity = full_name or username.strip()
    words = identity.split()
    initials = "".join(word[0] for word in words[:2]).upper() or "?"
    digest = hashlib.sha256(username.casefold().encode("utf-8")).digest()
    background = _FALLBACK_COLORS[digest[0] % len(_FALLBACK_COLORS)]
    safe_initials = html.escape(initials)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96" role="img">'
        f'<circle cx="48" cy="48" r="48" fill="{background}"/>'
        f'<text x="48" y="53" text-anchor="middle" dominant-baseline="middle" '
        f'font-family="Arial,sans-serif" font-size="34" font-weight="700" fill="#fff">'
        f"{safe_initials}</text></svg>"
    ).encode("utf-8")


def is_valid_stored_avatar(content: bytes) -> bool:
    if not content or len(content) > 1024 * 1024:
        return False
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format != "JPEG" or image.width > AVATAR_SIZE[0] or image.height > AVATAR_SIZE[1]:
                return False
            image.verify()
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError):
        return False
    return True


class SupabaseAvatarStorage:
    """Private Supabase Storage adapter; credentials are used only server-side."""

    def __init__(self, project_url: str, secret_key: str, bucket: str = "qbet-avatars") -> None:
        if not project_url or not secret_key:
            raise AvatarStorageError("Avatar storage is not configured.")
        self._base_url = project_url.rstrip("/")
        self._secret_key = secret_key
        self._bucket = quote(bucket, safe="")

    def _request(self, method: str, path: str, content: bytes | None = None) -> bytes:
        request = Request(
            f"{self._base_url}/storage/v1/object/{self._bucket}/{quote(path, safe='/')}",
            data=content,
            method=method,
            headers={
                "apikey": self._secret_key,
                "Authorization": f"Bearer {self._secret_key}",
                "Content-Type": "image/jpeg" if content is not None else "application/json",
                "x-upsert": "false",
            },
        )
        try:
            with urlopen(request, timeout=8) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError, OSError):
            raise AvatarStorageError("Avatar storage request failed.") from None

    def upload(self, object_key: str, content: bytes) -> None:
        self._request("POST", object_key, content)

    def download(self, object_key: str) -> bytes:
        return self._request("GET", object_key)

    def delete(self, object_key: str) -> None:
        request = Request(
            f"{self._base_url}/storage/v1/object/{self._bucket}",
            data=json.dumps({"prefixes": [object_key]}).encode("utf-8"),
            method="DELETE",
            headers={
                "apikey": self._secret_key,
                "Authorization": f"Bearer {self._secret_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=8):
                pass
        except (HTTPError, URLError, TimeoutError, OSError):
            raise AvatarStorageError("Avatar storage request failed.") from None


def get_avatar_storage() -> AvatarStorage:
    return SupabaseAvatarStorage(
        project_url=getattr(settings, "QBET_SUPABASE_URL", ""),
        secret_key=getattr(settings, "QBET_SUPABASE_STORAGE_SECRET_KEY", ""),
    )
