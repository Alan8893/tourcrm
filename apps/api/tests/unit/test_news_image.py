"""Unit tests for News image normalization (TH-0120 / Issue #227): the
shared upload decoder is reused, the aspect ratio is kept, the longest
side is bounded and the output is metadata-free WebP."""

import io

import pytest
from PIL import Image

from app.news.image import NEWS_IMAGE_MAX_SIDE, normalize_news_image
from app.people.photo_image import InvalidPhotoError, PhotoTooLargeError, normalize_profile_photo


def _png(size: tuple[int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, (10, 200, 30, 128)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_large_image_is_downscaled_keeping_aspect_ratio() -> None:
    output = normalize_news_image(_png((3200, 1600)))
    with Image.open(io.BytesIO(output)) as image:
        assert image.format == "WEBP"
        assert image.mode == "RGB"
        assert image.size == (NEWS_IMAGE_MAX_SIDE, NEWS_IMAGE_MAX_SIDE // 2)
        assert "exif" not in image.info


def test_small_image_is_not_upscaled() -> None:
    output = normalize_news_image(_png((400, 300)))
    with Image.open(io.BytesIO(output)) as image:
        assert image.size == (400, 300)


def test_invalid_and_oversized_uploads_are_rejected() -> None:
    with pytest.raises(InvalidPhotoError):
        normalize_news_image(b"not an image")
    with pytest.raises(InvalidPhotoError):
        normalize_news_image(b"")
    with pytest.raises(PhotoTooLargeError):
        normalize_news_image(b"0" * (10 * 1024 * 1024 + 1))


def test_profile_photo_normalization_is_unchanged_by_the_shared_decoder() -> None:
    output = normalize_profile_photo(_png((800, 400)))
    with Image.open(io.BytesIO(output)) as image:
        assert image.size == (512, 512)
        assert image.format == "WEBP"
