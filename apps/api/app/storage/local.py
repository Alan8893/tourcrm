"""Local filesystem `FileStorage` adapter (TH-0117.2 / Issue #158; ADR-0040
§3): a development/test-suitable implementation of `app.storage.
file_storage.FileStorage`, storing each object as one file under a
configured root directory.

`storage_key` stays opaque to callers (per the port's own contract) but,
for this adapter specifically, is a `/`-separated relative path used to
place the object under `root` (e.g. `documents/person/<opaque-id>`) —
this is this adapter's own concrete resolution rule, not part of the
`FileStorage` port's contract, and a future S3-compatible adapter is free
to interpret the same opaque `storage_key` differently (e.g. as an object
key with no filesystem meaning at all).

Path safety: `storage_key` is validated before ever touching the
filesystem. Backslashes, a leading `/`, a Windows drive prefix (`C:`), and
any `.`/`..`/empty path segment are rejected outright — this catches
Windows-style absolute/drive paths and mixed-separator traversal attempts
even when this adapter is running on a POSIX host, where such strings
would not otherwise be recognized as absolute by `pathlib`. The resulting
path is then resolved and checked to be a descendant of the resolved
`root` via `Path.relative_to` — never a `str.startswith` prefix check,
which would incorrectly treat a sibling directory sharing `root`'s string
prefix (e.g. configured root `/data/storage` and a resolved path under
`/data/storage-other`) as contained.

Immutability and the create-exclusive race: `put()` opens the destination
with `os.O_CREAT | os.O_EXCL`, the same primitive `open(..., "x")` uses.
The OS guarantees this succeeds for at most one of two concurrent callers
racing to create the same `storage_key`; the other observes `FileExistsError`
deterministically, which this adapter maps to `ObjectAlreadyExistsError`.
Because the file is never opened for writing unless creation itself
succeeded, a failed `put()` (whether from a pre-existing key or from a
write error partway through) never touches any content already stored
under that key.
"""

import os
import re
from pathlib import Path

from app.core.config import get_settings
from app.storage.file_storage import (
    FileStorage,
    InvalidStorageKeyError,
    ObjectAlreadyExistsError,
    ObjectNotFoundError,
)

_WINDOWS_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


def _reject_unsafe_key(storage_key: str) -> None:
    if not storage_key or not storage_key.strip():
        raise InvalidStorageKeyError(storage_key)
    if "\\" in storage_key:
        raise InvalidStorageKeyError(storage_key)
    if storage_key.startswith("/"):
        raise InvalidStorageKeyError(storage_key)
    if _WINDOWS_DRIVE_PREFIX.match(storage_key):
        raise InvalidStorageKeyError(storage_key)
    if any(segment in ("", ".", "..") for segment in storage_key.split("/")):
        raise InvalidStorageKeyError(storage_key)


class LocalFileStorage:
    """Stores each object as one file under `root` (created lazily —
    `root` need not exist yet when this adapter is constructed).
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def _resolve_path(self, storage_key: str) -> Path:
        _reject_unsafe_key(storage_key)
        candidate = (self._root / storage_key).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError:
            raise InvalidStorageKeyError(storage_key) from None
        return candidate

    def put(self, storage_key: str, content: bytes) -> None:
        path = self._resolve_path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ObjectAlreadyExistsError(storage_key) from None
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def get(self, storage_key: str) -> bytes:
        path = self._resolve_path(storage_key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise ObjectNotFoundError(storage_key) from None

    def exists(self, storage_key: str) -> bool:
        path = self._resolve_path(storage_key)
        return path.is_file()

    def delete(self, storage_key: str) -> None:
        path = self._resolve_path(storage_key)
        try:
            path.unlink()
        except FileNotFoundError:
            raise ObjectNotFoundError(storage_key) from None


def get_file_storage() -> FileStorage:
    """Dependency factory: `Depends(get_file_storage)` in a future endpoint,
    or call directly from application/service code. Mirrors
    `app.authentication.rate_limit.get_rate_limiter` — service code depends
    on the `FileStorage` port returned here, never on `LocalFileStorage`
    directly, so swapping in a future S3-compatible adapter is a one-line
    change to this function (or a `dependency_overrides` swap in tests).
    """
    settings = get_settings()
    return LocalFileStorage(root=settings.file_storage_root)


__all__ = ["LocalFileStorage", "get_file_storage"]
