from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from qbet.web.avatars import (
    AvatarValidationError,
    avatar_fallback_svg,
    is_valid_stored_avatar,
    normalize_avatar,
)


def _upload(image_format: str, content_type: str | None = None) -> SimpleUploadedFile:
    output = BytesIO()
    Image.new("RGB", (24, 18), color=(30, 120, 180)).save(output, format=image_format)
    mime_type = content_type or {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }[image_format]
    return SimpleUploadedFile("untrusted-name.bin", output.getvalue(), content_type=mime_type)


@pytest.mark.parametrize("image_format", ("JPEG", "PNG", "WEBP"))
def test_supported_images_are_normalized_to_metadata_free_jpeg(image_format: str) -> None:
    result = normalize_avatar(_upload(image_format))

    with Image.open(BytesIO(result.content)) as normalized:
        assert normalized.format == "JPEG"
        assert normalized.size == (24, 18)
        assert not normalized.getexif()


def test_image_content_type_must_match_detected_format() -> None:
    with pytest.raises(AvatarValidationError, match="does not match"):
        normalize_avatar(_upload("PNG", "image/jpeg"))


def test_corrupt_image_and_script_capable_format_are_rejected() -> None:
    corrupt = SimpleUploadedFile("photo.jpg", b"not an image", content_type="image/jpeg")
    svg = SimpleUploadedFile("avatar.svg", b"<svg/>", content_type="image/svg+xml")

    with pytest.raises(AvatarValidationError, match="not a valid image"):
        normalize_avatar(corrupt)
    with pytest.raises(AvatarValidationError, match="JPEG, PNG, or WebP"):
        normalize_avatar(svg)


def test_oversized_file_and_dimensions_are_rejected() -> None:
    oversized = SimpleUploadedFile(
        "large.png", b"x" * (5 * 1024 * 1024 + 1), content_type="image/png"
    )
    output = BytesIO()
    Image.new("RGB", (4097, 1)).save(output, format="PNG")
    too_wide = SimpleUploadedFile("wide.png", output.getvalue(), content_type="image/png")

    with pytest.raises(AvatarValidationError, match="5 MB"):
        normalize_avatar(oversized)
    with pytest.raises(AvatarValidationError, match="dimensions"):
        normalize_avatar(too_wide)


def test_fallback_avatar_is_stable_distinct_and_escapes_identity_text() -> None:
    first = avatar_fallback_svg("alice", "Alice", "Smith")

    assert first == avatar_fallback_svg("alice", "Alice", "Smith")
    assert first != avatar_fallback_svg("bob", "Bob", "Jones")
    assert b">AS</text>" in first
    assert b"<script>" not in avatar_fallback_svg("<script>")


def test_corrupt_or_oversized_stored_avatar_is_rejected() -> None:
    normalized = normalize_avatar(_upload("PNG")).content

    assert is_valid_stored_avatar(normalized)
    assert not is_valid_stored_avatar(b"broken image")
    assert not is_valid_stored_avatar(b"x" * (1024 * 1024 + 1))
