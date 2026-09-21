"""Pure-Python unit tests for the FileStorage port and its local filesystem
adapter (TH-0117.2 / Issue #158; ADR-0040 §3) — no database, no HTTP.

Every test uses pytest's `tmp_path` fixture as the storage root; none ever
touches the real `var/file-storage` default or any other on-disk location
outside its own per-test temporary directory.
"""

import pytest

from app.core.config import get_settings
from app.storage.file_storage import (
    FileStorage,
    InvalidStorageKeyError,
    ObjectAlreadyExistsError,
    ObjectNotFoundError,
)
from app.storage.local import LocalFileStorage, get_file_storage

# --- basic storage -----------------------------------------------------


def test_put_and_get_round_trip_returns_the_exact_original_bytes(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    content = b"\x00\x01hello world\xff"

    storage.put("document.bin", content)

    assert storage.get("document.bin") == content


def test_zero_byte_content_round_trips(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)

    storage.put("empty.bin", b"")

    assert storage.get("empty.bin") == b""


def test_nested_storage_key_is_stored_under_the_nested_path(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)

    storage.put("documents/person/abc-123/certificate.pdf", b"pdf-bytes")

    assert storage.get("documents/person/abc-123/certificate.pdf") == b"pdf-bytes"
    assert (tmp_path / "documents" / "person" / "abc-123" / "certificate.pdf").read_bytes() == (
        b"pdf-bytes"
    )


# --- missing object ------------------------------------------------------


def test_get_nonexistent_object_raises_object_not_found(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)

    with pytest.raises(ObjectNotFoundError):
        storage.get("never-stored.bin")


def test_exists_returns_false_for_a_nonexistent_object(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)

    assert storage.exists("never-stored.bin") is False


def test_exists_returns_true_after_put(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    storage.put("present.bin", b"x")

    assert storage.exists("present.bin") is True


def test_delete_nonexistent_object_raises_object_not_found(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)

    with pytest.raises(ObjectNotFoundError):
        storage.delete("never-stored.bin")


def test_delete_removes_a_stored_object(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    storage.put("to-delete.bin", b"x")

    storage.delete("to-delete.bin")

    assert storage.exists("to-delete.bin") is False


# --- immutability ---------------------------------------------------------


def test_put_on_an_existing_key_raises_object_already_exists(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    storage.put("certificate.pdf", b"version-1")

    with pytest.raises(ObjectAlreadyExistsError):
        storage.put("certificate.pdf", b"version-2")


def test_put_on_an_existing_key_with_identical_content_still_raises(tmp_path) -> None:
    # ADR-0040 §1: a File is immutable — put() never silently succeeds as a
    # no-op overwrite, even when the new content is byte-for-byte identical.
    storage = LocalFileStorage(root=tmp_path)
    storage.put("certificate.pdf", b"same-bytes")

    with pytest.raises(ObjectAlreadyExistsError):
        storage.put("certificate.pdf", b"same-bytes")


def test_original_content_is_unchanged_after_a_failed_overwrite_attempt(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    storage.put("certificate.pdf", b"original")

    with pytest.raises(ObjectAlreadyExistsError):
        storage.put("certificate.pdf", b"attempted-overwrite")

    assert storage.get("certificate.pdf") == b"original"


# --- path security ---------------------------------------------------------


@pytest.mark.parametrize(
    "unsafe_key",
    [
        pytest.param("../secret", id="parent-traversal"),
        pytest.param("../../etc/passwd", id="double-parent-traversal"),
        pytest.param("nested/../../escape", id="traversal-after-safe-prefix"),
        pytest.param("/etc/passwd", id="absolute-unix-path"),
        pytest.param(r"\Windows\System32\config", id="windows-absolute-path"),
        pytest.param(r"C:\Windows\System32\config", id="windows-drive-path-backslash"),
        pytest.param("C:/Windows/System32/config", id="windows-drive-path-forward-slash"),
        pytest.param("..\\../secret", id="mixed-separator-traversal"),
        pytest.param("a/..\\b", id="mixed-separator-traversal-embedded"),
        pytest.param("", id="empty-key"),
        pytest.param("   ", id="whitespace-only-key"),
        pytest.param("a//b", id="empty-segment"),
        pytest.param("a/./b", id="dot-segment"),
        pytest.param("trailing/slash/", id="trailing-slash"),
    ],
)
def test_unsafe_storage_keys_are_rejected_for_every_operation(tmp_path, unsafe_key) -> None:
    storage = LocalFileStorage(root=tmp_path)

    with pytest.raises(InvalidStorageKeyError):
        storage.put(unsafe_key, b"x")
    with pytest.raises(InvalidStorageKeyError):
        storage.get(unsafe_key)
    with pytest.raises(InvalidStorageKeyError):
        storage.exists(unsafe_key)
    with pytest.raises(InvalidStorageKeyError):
        storage.delete(unsafe_key)


def test_a_rejected_traversal_attempt_never_creates_a_file_outside_root(tmp_path) -> None:
    root = tmp_path / "storage"
    root.mkdir()

    with pytest.raises(InvalidStorageKeyError):
        LocalFileStorage(root=root).put("../escaped.bin", b"x")

    assert not (tmp_path / "escaped.bin").exists()


def test_a_symlink_resolving_outside_root_is_rejected(tmp_path) -> None:
    # The format-level checks reject any literal ".."/absolute segment, but
    # a resolvable symlink *inside* root can still point outside it. This
    # proves the adapter's containment check is a real `Path.resolve()` +
    # `relative_to()` test, not a `str.startswith()` prefix check on the
    # unresolved key.
    root = tmp_path / "storage"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"secret")
    (root / "escape").symlink_to(outside)

    storage = LocalFileStorage(root=root)

    with pytest.raises(InvalidStorageKeyError):
        storage.get("escape/secret.txt")


def test_a_sibling_directory_sharing_roots_string_prefix_is_not_treated_as_contained(
    tmp_path,
) -> None:
    # Regression guard for the classic str.startswith(root) bug: a sibling
    # directory whose name merely starts with the same characters as root
    # must not be reachable by resolving *into* root and escaping via a
    # symlink or relative construction that a naive prefix check would miss.
    root = tmp_path / "storage"
    root.mkdir()
    sibling = tmp_path / "storage-other"
    sibling.mkdir()
    (sibling / "secret.txt").write_bytes(b"secret")
    (root / "escape").symlink_to(sibling)

    storage = LocalFileStorage(root=root)

    with pytest.raises(InvalidStorageKeyError):
        storage.get("escape/secret.txt")


# --- isolation ---------------------------------------------------------


def test_an_object_stored_in_one_root_is_not_visible_through_a_different_root(tmp_path) -> None:
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    storage_a = LocalFileStorage(root=root_a)
    storage_b = LocalFileStorage(root=root_b)

    storage_a.put("shared-key.bin", b"a-content")

    assert storage_b.exists("shared-key.bin") is False
    with pytest.raises(ObjectNotFoundError):
        storage_b.get("shared-key.bin")


def test_storage_root_is_configurable_per_instance(tmp_path) -> None:
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    LocalFileStorage(root=root_a).put("key.bin", b"a")
    LocalFileStorage(root=root_b).put("key.bin", b"b")

    assert (root_a / "key.bin").read_bytes() == b"a"
    assert (root_b / "key.bin").read_bytes() == b"b"


# --- port (structural conformance) -----------------------------------------


def _round_trip_through_the_port(storage: FileStorage, key: str, content: bytes) -> bytes:
    """Exercises `storage` purely through the `FileStorage` Protocol type —
    proves `LocalFileStorage` is usable wherever the port is expected,
    with no reference to the concrete class beyond constructing it."""
    storage.put(key, content)
    assert storage.exists(key) is True
    result = storage.get(key)
    storage.delete(key)
    assert storage.exists(key) is False
    return result


def test_local_file_storage_is_usable_entirely_through_the_file_storage_port(tmp_path) -> None:
    storage: FileStorage = LocalFileStorage(root=tmp_path)

    result = _round_trip_through_the_port(storage, "a/b/c.bin", b"port-conformance-check")

    assert result == b"port-conformance-check"


# --- configuration / dependency injection -----------------------------------


def test_get_file_storage_resolves_a_local_adapter_rooted_at_the_configured_directory(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/synthetic_test_db"
    )
    monkeypatch.setenv("FILE_STORAGE_ROOT", str(tmp_path))

    storage = get_file_storage()

    assert isinstance(storage, LocalFileStorage)
    storage.put("probe.bin", b"probe-content")
    assert (tmp_path / "probe.bin").read_bytes() == b"probe-content"


def test_get_file_storage_uses_the_default_root_when_unconfigured(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/synthetic_test_db"
    )
    monkeypatch.delenv("FILE_STORAGE_ROOT", raising=False)

    settings = get_settings()

    assert settings.file_storage_root == "var/file-storage"
