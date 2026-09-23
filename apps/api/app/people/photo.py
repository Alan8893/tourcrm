"""Person profile photo lifecycle (TH-0119 / Issue #176).

Canonical sources: docs/05-api/profile-photo-api.md §2/§5/§6,
docs/06-ui/AVATAR-PHOTO-SPEC.md §7-8.

`Person.photo_file_id` references the one current photo `File`; there is
no avatar history. Binary content lives behind the existing `FileStorage`
port only — `storage_key` is generated here and never leaves the backend.

Authorization is not decided here: by the time any function below is
called, the API router has already resolved `person` through the existing
Person read/update policy.

Replacement order (profile-photo-api.md §5):

    normalize image                      # outside any DB transaction
    storage.put(new_key, webp)           # new immutable object
    try:
        BEGIN
          lock Person row
          File row for new object
          Person.photo_file_id = new File
          delete previous photo File row (no history)
          record_audit_event(person.updated)
        COMMIT
    except Exception:
        ROLLBACK
        storage.delete(new_key)          # compensation; old photo stays current
        raise
    storage.delete(old_key)              # only after commit; failure is logged,
                                         # never rolled back

Only a `File` whose `storage_key` sits under this Person's own photo
prefix is ever treated as (and therefore ever served or deleted as) that
Person's profile photo. `photo_file_id` is still an FK-less UUID column
that `PATCH /persons/{id}` historically accepted, so a reference to any
other `File` (e.g. a participant Document's) is never streamed by
`GET .../photo` nor removed from storage by `PUT`/`DELETE .../photo` —
those only clear the reference.
"""

import hashlib
import logging
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.db.documents import File
from app.db.identity import Person
from app.people.photo_image import PHOTO_OUTPUT_MIME_TYPE, normalize_profile_photo
from app.storage.file_storage import FileStorage, FileStorageError, ObjectNotFoundError

logger = logging.getLogger("tourcrm.people.photo")

_PHOTO_ORIGINAL_NAME = "profile-photo.webp"


def _photo_key_prefix(person_id: uuid.UUID) -> str:
    return f"photos/person/{person_id}/"


def _storage_key_for(person_id: uuid.UUID, file_id: uuid.UUID) -> str:
    return f"{_photo_key_prefix(person_id)}{file_id}"


def _current_photo_file(session: Session, person: Person) -> File | None:
    if person.photo_file_id is None:
        return None
    file_row = session.get(File, person.photo_file_id)
    if file_row is None or not file_row.storage_key.startswith(_photo_key_prefix(person.id)):
        return None
    return file_row


def _lock_person(session: Session, person: Person) -> None:
    session.execute(sa.select(Person.id).where(Person.id == person.id).with_for_update())
    session.refresh(person)


def _audit_photo_change(
    session: Session,
    *,
    person: Person,
    before: uuid.UUID | None,
    after: uuid.UUID | None,
    actor_user_id: uuid.UUID,
    request_id: str | None,
) -> None:
    record_audit_event(
        session,
        action="person.updated",
        actor_type="user",
        actor_user_id=actor_user_id,
        resource_type="person",
        resource_id=person.id,
        outcome="success",
        request_id=request_id,
        details={
            "changes": {
                "photo_file_id": {
                    "from": str(before) if before else None,
                    "to": str(after) if after else None,
                }
            }
        },
    )


def _delete_stored_object_after_commit(
    storage: FileStorage, *, storage_key: str, person_id: uuid.UUID, file_id: uuid.UUID
) -> None:
    try:
        storage.delete(storage_key)
    except ObjectNotFoundError:
        pass
    except FileStorageError:
        # The DB state is already committed and stays authoritative; the
        # leftover object falls under general orphan-file cleanup. Never
        # log image bytes (or the storage key).
        logger.warning(
            "person.photo.previous_object_delete_failed person_id=%s file_id=%s",
            person_id,
            file_id,
        )


def read_current_photo(
    session: Session, storage: FileStorage, *, person: Person
) -> tuple[uuid.UUID, bytes] | None:
    """Return `(file_id, webp_bytes)` for the current photo, or `None`."""
    file_row = _current_photo_file(session, person)
    if file_row is None:
        return None
    try:
        return file_row.id, storage.get(file_row.storage_key)
    except ObjectNotFoundError:
        return None


def set_profile_photo(
    session: Session,
    storage: FileStorage,
    *,
    person: Person,
    content: bytes,
    actor_user_id: uuid.UUID,
    request_id: str | None = None,
) -> Person:
    """Create or replace `person`'s current photo. Raises
    `app.people.photo_image.InvalidPhotoError`/`PhotoTooLargeError`
    before anything is stored."""
    webp = normalize_profile_photo(content)

    file_id = uuid.uuid4()
    storage_key = _storage_key_for(person.id, file_id)
    storage.put(storage_key, webp)

    previous: tuple[str, uuid.UUID] | None = None
    try:
        _lock_person(session, person)
        before = person.photo_file_id
        previous_file = _current_photo_file(session, person)

        session.add(
            File(
                id=file_id,
                storage_key=storage_key,
                original_name=_PHOTO_ORIGINAL_NAME,
                mime_type=PHOTO_OUTPUT_MIME_TYPE,
                size_bytes=len(webp),
                checksum=hashlib.sha256(webp).hexdigest(),
                storage_backend=storage.backend_name,
                created_by=actor_user_id,
            )
        )
        session.flush()
        person.photo_file_id = file_id
        if previous_file is not None:
            previous = (previous_file.storage_key, previous_file.id)
            session.delete(previous_file)
        session.flush()
        _audit_photo_change(
            session,
            person=person,
            before=before,
            after=file_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        session.commit()
    except Exception:
        session.rollback()
        try:
            storage.delete(storage_key)
        except FileStorageError:
            logger.warning(
                "person.photo.compensation_delete_failed person_id=%s file_id=%s",
                person.id,
                file_id,
            )
        raise

    if previous is not None:
        _delete_stored_object_after_commit(
            storage, storage_key=previous[0], person_id=person.id, file_id=previous[1]
        )
    return person


def delete_profile_photo(
    session: Session,
    storage: FileStorage,
    *,
    person: Person,
    actor_user_id: uuid.UUID,
    request_id: str | None = None,
) -> None:
    """Clear `person`'s current photo and remove its stored object. A
    Person with no current photo is a successful no-op (repeated DELETE
    is safe)."""
    previous: tuple[str, uuid.UUID] | None = None
    try:
        _lock_person(session, person)
        before = person.photo_file_id
        if before is None:
            session.rollback()
            return
        previous_file = _current_photo_file(session, person)
        person.photo_file_id = None
        if previous_file is not None:
            previous = (previous_file.storage_key, previous_file.id)
            session.delete(previous_file)
        session.flush()
        _audit_photo_change(
            session,
            person=person,
            before=before,
            after=None,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    if previous is not None:
        _delete_stored_object_after_commit(
            storage, storage_key=previous[0], person_id=person.id, file_id=previous[1]
        )


__all__ = ["read_current_photo", "set_profile_photo", "delete_profile_photo"]
