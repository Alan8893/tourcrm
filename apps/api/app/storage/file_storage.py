"""The `FileStorage` port (TH-0117.2 / Issue #158; ADR-0040 §3).

A minimal, backend-independent contract for storing and retrieving binary
content addressed only by an opaque `storage_key`. Mirrors
`app.authentication.rate_limit.RateLimiter`'s own shape: a `typing.Protocol`
contract plus the errors it raises, so a concrete adapter (e.g.
`app.storage.local.LocalFileStorage`, a future S3-compatible adapter) needs
no shared base class — only to satisfy this structural interface.

This module never imports SQLAlchemy, FastAPI, or any ORM model. It has no
knowledge of `Person`, `Document`, `Event`, HTTP, authorization,
permissions, or audit — those are all callers of this port, never part of
it (ADR-0040 §3's "business/domain code never depends on filesystem or
object-storage details directly", read in the other direction: this port
never depends on business/domain code either).

`storage_key` is caller-supplied and opaque to this port — this module
does not define or constrain its format beyond what is required for a
backend to store it safely (see `app.storage.local` for the local
adapter's own concrete safety rules). It is never a filesystem path, a
public URL, or a presigned URL in this port's contract.

Immutability (ADR-0040 §1): a `File` artifact is immutable once created.
`put()` must fail with `ObjectAlreadyExistsError` if `storage_key` already
has stored content — including when the new content is byte-for-byte
identical to the existing content. There is no update/replace operation on
this port; replacing a Document's file is a higher-level operation (a new
`File` row with a new `storage_key`, plus a new `Document` version) that
this port has no knowledge of.
"""

from typing import Protocol


class FileStorage(Protocol):
    """Contract a concrete storage backend must satisfy."""

    @property
    def backend_name(self) -> str:
        """A short, stable identifier for this backend (e.g. `"local"`),
        persisted verbatim as `File.storage_backend` (ADR-0040 §1) by a
        caller that creates a `File` row after a successful `put()`. Lets
        that caller populate the column without hardcoding which concrete
        adapter is currently wired in — see
        `app.storage.local.LocalFileStorage.backend_name`.
        """
        ...

    def put(self, storage_key: str, content: bytes) -> None:
        """Store `content` under `storage_key`.

        Must raise `ObjectAlreadyExistsError` without writing anything if
        `storage_key` already has stored content — this operation never
        overwrites, silently or otherwise. Must raise
        `InvalidStorageKeyError` if `storage_key` is not a key this backend
        can safely store.
        """
        ...

    def get(self, storage_key: str) -> bytes:
        """Return the content previously stored under `storage_key`.

        Raises `ObjectNotFoundError` if no content is stored under that
        key. Raises `InvalidStorageKeyError` if `storage_key` is not a key
        this backend can safely resolve.
        """
        ...

    def exists(self, storage_key: str) -> bool:
        """Return whether content is currently stored under `storage_key`.

        Raises `InvalidStorageKeyError` if `storage_key` is not a key this
        backend can safely resolve. Never raises `ObjectNotFoundError` —
        a missing object is exactly what this method returns `False` for.
        """
        ...

    def delete(self, storage_key: str) -> None:
        """Remove the content stored under `storage_key`.

        Raises `ObjectNotFoundError` if no content is stored under that
        key. Raises `InvalidStorageKeyError` if `storage_key` is not a key
        this backend can safely resolve. Retention/lifecycle policy for
        *when* a File may be deleted (ADR-0040 §3: never while still
        referenced by retained Document history) is decided by the
        caller — this port only performs the removal once asked.
        """
        ...


class FileStorageError(Exception):
    """Base class for this port's typed, expected failures."""


class InvalidStorageKeyError(FileStorageError):
    """`storage_key` is not one this backend can safely store or resolve
    (e.g. it attempts to escape a local adapter's configured root)."""

    def __init__(self, storage_key: str) -> None:
        super().__init__(f"Invalid storage key: {storage_key!r}")
        self.storage_key = storage_key


class ObjectNotFoundError(FileStorageError):
    """No content is currently stored under `storage_key`."""

    def __init__(self, storage_key: str) -> None:
        super().__init__(f"No stored object for storage key: {storage_key!r}")
        self.storage_key = storage_key


class ObjectAlreadyExistsError(FileStorageError):
    """`storage_key` already has stored content.

    A `File` artifact is immutable (ADR-0040 §1) — `put()` never
    overwrites an existing object, even when the new content is identical
    to what is already stored.
    """

    def __init__(self, storage_key: str) -> None:
        super().__init__(f"Storage key already exists and is immutable: {storage_key!r}")
        self.storage_key = storage_key


__all__ = [
    "FileStorage",
    "FileStorageError",
    "InvalidStorageKeyError",
    "ObjectNotFoundError",
    "ObjectAlreadyExistsError",
]
