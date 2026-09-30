"""News image storage (TH-0120 / Issue #227).

Reuses the existing file mechanism end to end — no second storage
mechanism: the upload is validated/decoded by the shared
app.people.photo_image decoder, stored through the `FileStorage` port
(ADR-0040 §3) and referenced through a row of the domain-neutral `files`
table, exactly like the Person profile photo (app.people.photo), whose
replacement order this module mirrors:

    normalize image                  # outside any DB transaction
    storage.put(new_key, webp)       # new immutable object
    BEGIN; File row; News.image_file_id = new; delete previous File row; COMMIT
    (on failure: ROLLBACK + storage.delete(new_key) compensation)
    storage.delete(old_key)          # only after commit, failure logged

The News image is not versioned (one current image, like the avatar): a
replacement or removal drops the previous `File` row and object. The News
record itself is never deleted; archiving keeps its image as-is.

The stored representation is an RGB WebP whose longest side is at most
`NEWS_IMAGE_MAX_SIDE` pixels, aspect ratio preserved (News cards are not
square), with no source metadata.
"""

import hashlib
import logging
import uuid

from PIL import Image
from sqlalchemy.orm import Session

from app.db.documents import File
from app.db.news import News
from app.people.photo_image import (
    PHOTO_OUTPUT_MIME_TYPE,
    decode_uploaded_image,
    encode_webp,
)
from app.storage.file_storage import FileStorage, FileStorageError, ObjectNotFoundError

logger = logging.getLogger("tourcrm.news.image")

NEWS_IMAGE_MAX_SIDE = 1600
NEWS_IMAGE_MIME_TYPE = PHOTO_OUTPUT_MIME_TYPE
_ORIGINAL_NAME = "news-image.webp"


def _key_prefix(news_id: uuid.UUID) -> str:
    return f"news/{news_id}/"


def normalize_news_image(content: bytes) -> bytes:
    """Raises app.people.photo_image.PhotoTooLargeError/InvalidPhotoError."""
    image = decode_uploaded_image(content)
    image.thumbnail((NEWS_IMAGE_MAX_SIDE, NEWS_IMAGE_MAX_SIDE), Image.Resampling.LANCZOS)
    return encode_webp(image)


def _current_image_file(session: Session, news: News) -> File | None:
    if news.image_file_id is None:
        return None
    file_row = session.get(File, news.image_file_id)
    if file_row is None or not file_row.storage_key.startswith(_key_prefix(news.id)):
        return None
    return file_row


def read_news_image(
    session: Session, storage: FileStorage, *, news: News
) -> tuple[uuid.UUID, bytes] | None:
    file_row = _current_image_file(session, news)
    if file_row is None:
        return None
    try:
        return file_row.id, storage.get(file_row.storage_key)
    except ObjectNotFoundError:
        return None


def _delete_object_after_commit(
    storage: FileStorage, *, storage_key: str, news_id: uuid.UUID
) -> None:
    try:
        storage.delete(storage_key)
    except ObjectNotFoundError:
        pass
    except FileStorageError:
        logger.warning("news.image.previous_object_delete_failed news_id=%s", news_id)


def set_news_image(
    session: Session,
    storage: FileStorage,
    *,
    news: News,
    content: bytes,
    actor_user_id: uuid.UUID,
) -> News:
    """Create or replace the News image. The caller has already locked
    `news` and checked it is editable; nothing is committed before the
    image is validated."""
    webp = normalize_news_image(content)

    file_id = uuid.uuid4()
    storage_key = f"{_key_prefix(news.id)}{file_id}"
    storage.put(storage_key, webp)

    previous_key: str | None = None
    try:
        previous_file = _current_image_file(session, news)
        session.add(
            File(
                id=file_id,
                storage_key=storage_key,
                original_name=_ORIGINAL_NAME,
                mime_type=NEWS_IMAGE_MIME_TYPE,
                size_bytes=len(webp),
                checksum=hashlib.sha256(webp).hexdigest(),
                storage_backend=storage.backend_name,
                created_by=actor_user_id,
            )
        )
        session.flush()
        news.image_file_id = file_id
        news.updated_by = actor_user_id
        session.flush()
        if previous_file is not None:
            previous_key = previous_file.storage_key
            session.delete(previous_file)
            session.flush()
        session.commit()
    except Exception:
        session.rollback()
        try:
            storage.delete(storage_key)
        except FileStorageError:
            logger.warning("news.image.compensation_delete_failed news_id=%s", news.id)
        raise

    if previous_key is not None:
        _delete_object_after_commit(storage, storage_key=previous_key, news_id=news.id)
    return news


def delete_news_image(
    session: Session, storage: FileStorage, *, news: News, actor_user_id: uuid.UUID
) -> News:
    """Remove the News image (the News itself stays). No current image is
    a successful no-op."""
    if news.image_file_id is None:
        session.rollback()
        return news
    previous_key: str | None = None
    try:
        previous_file = _current_image_file(session, news)
        news.image_file_id = None
        news.updated_by = actor_user_id
        session.flush()
        if previous_file is not None:
            previous_key = previous_file.storage_key
            session.delete(previous_file)
            session.flush()
        session.commit()
    except Exception:
        session.rollback()
        raise

    if previous_key is not None:
        _delete_object_after_commit(storage, storage_key=previous_key, news_id=news.id)
    return news


__all__ = [
    "NEWS_IMAGE_MAX_SIDE",
    "NEWS_IMAGE_MIME_TYPE",
    "normalize_news_image",
    "read_news_image",
    "set_news_image",
    "delete_news_image",
]
