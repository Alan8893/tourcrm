"""Unit tests for profile photo normalization (TH-0119 / Issue #176;
profile-photo-api.md §2/§7/§9). Pure image processing — no DB/HTTP."""

import io

import pytest
from PIL import Image

from app.people.photo_image import (
    MAX_PHOTO_UPLOAD_BYTES,
    InvalidPhotoError,
    PhotoTooLargeError,
    normalize_profile_photo,
)

RED = (255, 0, 0)
BLUE = (0, 0, 255)


def _encode(image: Image.Image, fmt: str, **kwargs: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def _top_red_bottom_blue(size: int = 200, mode: str = "RGB") -> Image.Image:
    image = Image.new(mode, (size, size), BLUE if mode == "RGB" else BLUE + (255,))
    image.paste(RED if mode == "RGB" else RED + (255,), (0, 0, size, size // 2))
    return image


def _decode(content: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(content))
    image.load()
    return image


def _is_close(pixel: tuple[int, ...], expected: tuple[int, int, int], tolerance: int = 40) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(pixel[:3], expected))


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_accepted_formats_produce_512_square_rgb_webp(fmt: str) -> None:
    output = normalize_profile_photo(_encode(_top_red_bottom_blue(), fmt))

    result = _decode(output)
    assert result.format == "WEBP"
    assert result.size == (512, 512)
    assert result.mode == "RGB"
    assert _is_close(result.getpixel((256, 64)), RED)
    assert _is_close(result.getpixel((256, 448)), BLUE)


def test_output_carries_no_source_exif() -> None:
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"
    output = normalize_profile_photo(_encode(_top_red_bottom_blue(), "JPEG", exif=exif))

    assert b"SecretCameraMaker" not in output
    assert not _decode(output).getexif()


def test_exif_orientation_is_normalized() -> None:
    # Orientation 6 = "rotate 90° clockwise to display": the stored top
    # (red) half must end up on the right of the normalized output.
    exif = Image.Exif()
    exif[0x0112] = 6
    output = normalize_profile_photo(_encode(_top_red_bottom_blue(), "JPEG", exif=exif))

    result = _decode(output)
    assert _is_close(result.getpixel((448, 256)), RED)
    assert _is_close(result.getpixel((64, 256)), BLUE)


def test_transparency_is_flattened_not_kept() -> None:
    image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    output = normalize_profile_photo(_encode(image, "PNG"))

    result = _decode(output)
    assert result.mode == "RGB"
    assert _is_close(result.getpixel((256, 256)), (255, 255, 255))


def test_non_square_source_is_normalized_to_square() -> None:
    image = Image.new("RGB", (600, 300), RED)
    result = _decode(normalize_profile_photo(_encode(image, "PNG")))
    assert result.size == (512, 512)


def test_fake_image_with_image_extension_content_is_rejected() -> None:
    with pytest.raises(InvalidPhotoError):
        normalize_profile_photo(b"%PDF-1.4 definitely not an image")


def test_truncated_image_is_rejected() -> None:
    content = _encode(_top_red_bottom_blue(), "PNG")
    with pytest.raises(InvalidPhotoError):
        normalize_profile_photo(content[: len(content) // 2])


def test_unsupported_real_image_format_is_rejected() -> None:
    with pytest.raises(InvalidPhotoError):
        normalize_profile_photo(_encode(_top_red_bottom_blue(), "GIF"))


def test_empty_upload_is_rejected() -> None:
    with pytest.raises(InvalidPhotoError):
        normalize_profile_photo(b"")


def test_upload_over_10_mb_is_rejected() -> None:
    with pytest.raises(PhotoTooLargeError):
        normalize_profile_photo(b"\0" * (MAX_PHOTO_UPLOAD_BYTES + 1))


def test_decompression_bomb_dimensions_are_rejected() -> None:
    # A tiny PNG whose header claims a huge canvas: rejected from the
    # header, before decoding pixel data.
    bomb = _encode(Image.new("1", (10000, 10000)), "PNG")
    assert len(bomb) < MAX_PHOTO_UPLOAD_BYTES
    with pytest.raises(InvalidPhotoError):
        normalize_profile_photo(bomb)
