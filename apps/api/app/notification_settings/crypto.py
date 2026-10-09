"""Encryption of UI-managed integration secrets (Issue #333, ADR-0048 §2.6).

AES-256-GCM through `cryptography` — no custom cryptography.

Key ring: `SETTINGS_ENCRYPTION_KEYS` (deployment secret configuration) is a
comma-separated list of `<key_id>:<base64 key>` entries, each key exactly
32 bytes (standard or URL-safe base64, padding optional). The first entry
encrypts every new value; the others are accepted only for decryption, so
a rotation can add a new key before the old values are re-encrypted
(app.cli.reencrypt_settings_secrets). Key ids are public labels
(`[A-Za-z0-9_-]{1,32}`); key bytes never leave this module's objects and
are excluded from every repr.

Stored token (text, ASCII): `v1.<key_id>.<nonce>.<ciphertext>` — `nonce`
is a fresh random 96-bit value per encryption and `ciphertext` the AES-GCM
output including its 128-bit authentication tag, both unpadded URL-safe
base64. The secret's identifier is the associated data
(`tourcrm.settings_secret.v1:<name>`), so a token copied into another
secret slot fails authentication.

Every failure is fail closed and carries only a stable code: a missing or
malformed key ring (KeyRingUnavailable), an unknown key id, a wrong key, a
tampered token or an unsupported format (SecretDecryptionError). Nothing
here ever falls back to plaintext or to another configuration source, and
no exception message contains a key, a token or a secret.
"""

import base64
import binascii
import os
import re
import secrets
from dataclasses import dataclass, field
from typing import Literal, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_RING_ENV = "SETTINGS_ENCRYPTION_KEYS"
TOKEN_VERSION = "v1"
KEY_BYTES = 32
NONCE_BYTES = 12
_KEY_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}")
_AAD_PREFIX = b"tourcrm.settings_secret.v1:"

KeyRingFailure = Literal["missing", "invalid"]


class KeyRingUnavailable(Exception):
    """The key ring is not configured (`missing`) or is malformed
    (`invalid`). `str()` is a safe operational message."""

    def __init__(self, reason: KeyRingFailure) -> None:
        message = (
            f"{KEY_RING_ENV} is not set"
            if reason == "missing"
            else f"{KEY_RING_ENV} is invalid (expected <key_id>:<base64 32-byte key>[,...])"
        )
        super().__init__(message)
        self.reason = reason


class SecretDecryptionError(Exception):
    """A stored secret cannot be decrypted with the configured key ring.
    `code` is a stable, safe identifier."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EncryptionKey:
    key_id: str
    material: bytes = field(repr=False)


def _decode_key(raw: str) -> Optional[bytes]:
    """32 key bytes from standard or URL-safe base64 (padding optional)."""
    text = raw.strip()
    if not re.fullmatch(r"[A-Za-z0-9+/_-]+={0,2}", text):
        return None
    padded = text.rstrip("=")
    padded += "=" * (-len(padded) % 4)
    try:
        value = base64.b64decode(padded.replace("-", "+").replace("_", "/"), validate=True)
    except (binascii.Error, ValueError):
        return None
    return value if len(value) == KEY_BYTES else None


@dataclass(frozen=True)
class KeyRing:
    """Ordered keys; `keys[0]` is the primary (encryption) key."""

    keys: tuple[EncryptionKey, ...]

    def __post_init__(self) -> None:
        if not self.keys:
            raise KeyRingUnavailable("missing")
        ids = [key.key_id for key in self.keys]
        if len(set(ids)) != len(ids):
            raise KeyRingUnavailable("invalid")

    def __repr__(self) -> str:
        return f"KeyRing(key_ids={[key.key_id for key in self.keys]!r})"

    @property
    def primary(self) -> EncryptionKey:
        return self.keys[0]

    def get(self, key_id: str) -> Optional[EncryptionKey]:
        return next((key for key in self.keys if key.key_id == key_id), None)

    @classmethod
    def parse(cls, value: Optional[str]) -> "KeyRing":
        if value is None or not value.strip():
            raise KeyRingUnavailable("missing")
        keys: list[EncryptionKey] = []
        for entry in value.split(","):
            key_id, separator, raw_key = entry.strip().partition(":")
            if not separator or not _KEY_ID_PATTERN.fullmatch(key_id):
                raise KeyRingUnavailable("invalid")
            material = _decode_key(raw_key)
            if material is None:
                raise KeyRingUnavailable("invalid")
            keys.append(EncryptionKey(key_id=key_id, material=material))
        return cls(tuple(keys))

    @classmethod
    def from_env(cls) -> "KeyRing":
        """Read and validate `SETTINGS_ENCRYPTION_KEYS` now (no caching, so
        tests and processes always see the current deployment value)."""
        return cls.parse(os.getenv(KEY_RING_ENV))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    if not text or not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _aad(secret_name: str) -> bytes:
    return _AAD_PREFIX + secret_name.encode("utf-8")


def encrypt_secret(key_ring: KeyRing, secret_name: str, plaintext: str) -> str:
    """Encrypt `plaintext` with the primary key; returns the stored token."""
    key = key_ring.primary
    nonce = secrets.token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(key.material).encrypt(
        nonce, plaintext.encode("utf-8"), _aad(secret_name)
    )
    return ".".join((TOKEN_VERSION, key.key_id, _b64(nonce), _b64(ciphertext)))


def token_key_id(token: str) -> Optional[str]:
    """The key id a stored token claims, or None for an unparseable token."""
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != TOKEN_VERSION or not _KEY_ID_PATTERN.fullmatch(parts[1]):
        return None
    return parts[1]


def decrypt_secret(key_ring: KeyRing, secret_name: str, token: str) -> str:
    """Decrypt a stored token. Raises SecretDecryptionError (fail closed)."""
    parts = token.split(".")
    if len(parts) != 4:
        raise SecretDecryptionError("secret_format_invalid")
    version, key_id, nonce_text, ciphertext_text = parts
    if version != TOKEN_VERSION:
        raise SecretDecryptionError("secret_format_unsupported")
    key = key_ring.get(key_id)
    if key is None:
        raise SecretDecryptionError("secret_key_unknown")
    failed = False
    try:
        nonce = _unb64(nonce_text)
        ciphertext = _unb64(ciphertext_text)
    except (ValueError, binascii.Error):
        failed = True
    if failed or len(nonce) != NONCE_BYTES or len(ciphertext) < 16:
        raise SecretDecryptionError("secret_format_invalid")
    try:
        plaintext = AESGCM(key.material).decrypt(nonce, ciphertext, _aad(secret_name))
    except InvalidTag:
        failed = True
    if failed:
        # Raised outside the handler: no exception context is kept.
        raise SecretDecryptionError("secret_authentication_failed")
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError:
        raise SecretDecryptionError("secret_format_invalid") from None


__all__ = [
    "KEY_RING_ENV",
    "TOKEN_VERSION",
    "KeyRingUnavailable",
    "SecretDecryptionError",
    "EncryptionKey",
    "KeyRing",
    "encrypt_secret",
    "decrypt_secret",
    "token_key_id",
]
