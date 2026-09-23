"""Profile photo image validation and normalization (TH-0119 / Issue #176).

Canonical sources: docs/06-ui/AVATAR-PHOTO-SPEC.md §5-6,
docs/05-api/profile-photo-api.md §2/§7.

The backend is authoritative for the stored representation even though
the frontend provides the crop UX: whatever bytes arrive are decoded from
their actual content (never the client-supplied MIME type or filename),
checked against the JPEG/PNG/WebP allowlist and a pixel-count ceiling
(decompression-bomb protection), EXIF-orientation normalized, flattened
to RGB and re-encoded as a square 512×512 WebP with no metadata. The
original upload is never returned or stored by this module.

Pure image processing only — no database, storage, HTTP or authorization
knowledge.
"""

import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PHOTO_UPLOAD_BYTES = 10 * 1024 * 1024
PHOTO_OUTPUT_SIZE = 512
PHOTO_OUTPUT_MIME_TYPE = "image/webp"

_ALLOWED_SOURCE_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
# Decompression-bomb ceiling, checked from the header before any pixel
# data is decoded: a legitimate phone/camera photo is well under this,
# while a tiny compressed file claiming e.g. 50000×50000 is rejected
# without ever allocating its decoded bitmap.
_MAX_SOURCE_PIXELS = 50_000_000
_WEBP_QUALITY = 85


class InvalidPhotoError(Exception):
    """The upload is not a decodable JPEG/PNG/WebP image within limits."""


class PhotoTooLargeError(Exception):
    """The upload exceeds `MAX_PHOTO_UPLOAD_BYTES`."""


def normalize_profile_photo(content: bytes) -> bytes:
    """Return the canonical stored representation of `content`: a
    512×512 RGB WebP. Raises `PhotoTooLargeError`/`InvalidPhotoError`.

    A non-square source (the frontend always sends its square crop, but
    the backend does not trust that) is reduced to its centered square —
    a deterministic normalization, not an automatic composition choice.
    """
    if len(content) > MAX_PHOTO_UPLOAD_BYTES:
        raise PhotoTooLargeError()
    if not content:
        raise InvalidPhotoError("Empty upload")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as probe:
                if probe.format not in _ALLOWED_SOURCE_FORMATS:
                    raise InvalidPhotoError("Unsupported image format")
                width, height = probe.size
                if width <= 0 or height <= 0 or width * height > _MAX_SOURCE_PIXELS:
                    raise InvalidPhotoError("Image dimensions out of range")
                probe.verify()

            # `verify()` leaves the image unusable; reopen to decode.
            with Image.open(io.BytesIO(content)) as image:
                image.seek(0)
                image.load()
                oriented = ImageOps.exif_transpose(image)
                rgb = _flatten_to_rgb(oriented)
    except InvalidPhotoError:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise InvalidPhotoError("Image content could not be decoded") from exc

    square = ImageOps.fit(
        rgb,
        (PHOTO_OUTPUT_SIZE, PHOTO_OUTPUT_SIZE),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    output = io.BytesIO()
    # No `exif=`/`icc_profile=` passed: the stored image carries no source
    # metadata (EXIF/GPS never survives normalization).
    square.save(output, format="WEBP", quality=_WEBP_QUALITY)
    return output.getvalue()


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    """Standard web RGB output with no transparency: any alpha channel is
    composited onto white rather than dropped (which would expose
    whatever colour data sits under fully transparent pixels)."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


__all__ = [
    "MAX_PHOTO_UPLOAD_BYTES",
    "PHOTO_OUTPUT_SIZE",
    "PHOTO_OUTPUT_MIME_TYPE",
    "InvalidPhotoError",
    "PhotoTooLargeError",
    "normalize_profile_photo",
]
