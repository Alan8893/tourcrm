"""Unit tests for the settings secret encryption (Issue #333, ADR-0048
§2.6): AES-256-GCM token format, key ring parsing and validation, wrong
key, unknown key id, tampering, slot binding (associated data), rotation,
and that no key or secret ever appears in a repr or an error. Test-only
keys; no database."""

import base64
import secrets

import pytest

from app.notification_settings.crypto import (
    KEY_RING_ENV,
    KeyRing,
    KeyRingUnavailable,
    SecretDecryptionError,
    decrypt_secret,
    encrypt_secret,
    token_key_id,
)
from tests.notification_settings_helpers import TEST_KEY_A, TEST_KEY_B

SECRET = "s3cr3t-smtp-pa55word-Ω"
RING_A = KeyRing.parse(f"a:{TEST_KEY_A}")
RING_B = KeyRing.parse(f"b:{TEST_KEY_B}")


def _flip(text: str, index: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    replacement = alphabet[(alphabet.index(text[index]) + 1) % len(alphabet)]
    return text[:index] + replacement + text[index + 1 :]


def test_round_trip_and_token_format() -> None:
    token = encrypt_secret(RING_A, "smtp_password", SECRET)
    version, key_id, nonce, ciphertext = token.split(".")
    assert (version, key_id) == ("v1", "a")
    assert len(base64.urlsafe_b64decode(nonce + "==")) == 12
    # Ciphertext = plaintext bytes + 16-byte GCM tag.
    assert len(base64.urlsafe_b64decode(ciphertext + "==")) == len(SECRET.encode()) + 16
    assert SECRET not in token
    assert decrypt_secret(RING_A, "smtp_password", token) == SECRET
    assert token_key_id(token) == "a"


def test_every_encryption_uses_a_fresh_nonce() -> None:
    tokens = {encrypt_secret(RING_A, "smtp_password", SECRET) for _ in range(20)}
    assert len(tokens) == 20
    assert len({token.split(".")[2] for token in tokens}) == 20


def test_ciphertext_is_bound_to_its_secret_slot() -> None:
    token = encrypt_secret(RING_A, "smtp_password", SECRET)
    with pytest.raises(SecretDecryptionError) as raised:
        decrypt_secret(RING_A, "telegram_bot_token", token)
    assert raised.value.code == "secret_authentication_failed"


def test_wrong_key_with_the_same_id_fails_closed() -> None:
    token = encrypt_secret(RING_A, "smtp_password", SECRET)
    wrong = KeyRing.parse(f"a:{TEST_KEY_B}")
    with pytest.raises(SecretDecryptionError) as raised:
        decrypt_secret(wrong, "smtp_password", token)
    assert raised.value.code == "secret_authentication_failed"
    assert raised.value.__context__ is None


def test_unknown_key_id_fails_closed() -> None:
    token = encrypt_secret(RING_A, "smtp_password", SECRET)
    with pytest.raises(SecretDecryptionError) as raised:
        decrypt_secret(RING_B, "smtp_password", token)
    assert raised.value.code == "secret_key_unknown"


@pytest.mark.parametrize("part", [2, 3])
def test_tampered_nonce_or_ciphertext_fails_closed(part: int) -> None:
    token = encrypt_secret(RING_A, "smtp_password", SECRET)
    parts = token.split(".")
    parts[part] = _flip(parts[part], 3)
    with pytest.raises(SecretDecryptionError):
        decrypt_secret(RING_A, "smtp_password", ".".join(parts))


def test_truncated_tag_fails_closed() -> None:
    token = encrypt_secret(RING_A, "smtp_password", SECRET)
    with pytest.raises(SecretDecryptionError):
        decrypt_secret(RING_A, "smtp_password", token[:-4])


@pytest.mark.parametrize(
    ("token", "code"),
    [
        ("", "secret_format_invalid"),
        ("plaintext-password", "secret_format_invalid"),
        ("v1.a.only-three", "secret_format_invalid"),
        ("v2.a.AAAA.BBBB", "secret_format_unsupported"),
        ("v1.a.!!!!.BBBB", "secret_format_invalid"),
        ("v1.a.AAAA.BBBB", "secret_format_invalid"),
    ],
)
def test_malformed_tokens_fail_closed(token: str, code: str) -> None:
    with pytest.raises(SecretDecryptionError) as raised:
        decrypt_secret(RING_A, "smtp_password", token)
    assert raised.value.code == code


def test_rotation_primary_encrypts_secondary_still_decrypts() -> None:
    old_token = encrypt_secret(RING_A, "smtp_password", SECRET)
    rotated = KeyRing.parse(f"b:{TEST_KEY_B},a:{TEST_KEY_A}")
    assert rotated.primary.key_id == "b"
    assert decrypt_secret(rotated, "smtp_password", old_token) == SECRET
    new_token = encrypt_secret(rotated, "smtp_password", SECRET)
    assert token_key_id(new_token) == "b"
    # Once the old key is removed, only re-encrypted values remain readable.
    with pytest.raises(SecretDecryptionError):
        decrypt_secret(RING_B, "smtp_password", old_token)
    assert decrypt_secret(RING_B, "smtp_password", new_token) == SECRET


def test_key_ring_accepts_standard_and_url_safe_base64_with_or_without_padding() -> None:
    raw = secrets.token_bytes(32)
    for encoded in (
        base64.b64encode(raw).decode(),
        base64.urlsafe_b64encode(raw).decode(),
        base64.urlsafe_b64encode(raw).decode().rstrip("="),
    ):
        ring = KeyRing.parse(f" k1 : {encoded} ".replace(" : ", ":"))
        assert ring.primary.material == raw


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (None, "missing"),
        ("", "missing"),
        ("   ", "missing"),
        (f"{TEST_KEY_A}", "invalid"),
        (f"a:{base64.b64encode(b'short').decode()}", "invalid"),
        (f"a:{base64.b64encode(secrets.token_bytes(31)).decode()}", "invalid"),
        (f"a:{base64.b64encode(secrets.token_bytes(64)).decode()}", "invalid"),
        ("a:not base64 at all", "invalid"),
        (f"bad id:{TEST_KEY_A}", "invalid"),
        (f"a.b:{TEST_KEY_A}", "invalid"),
        (f"a:{TEST_KEY_A},a:{TEST_KEY_B}", "invalid"),
        (f"a:{TEST_KEY_A},", "invalid"),
    ],
)
def test_invalid_key_rings_fail_closed(value: object, reason: str) -> None:
    with pytest.raises(KeyRingUnavailable) as raised:
        KeyRing.parse(value)  # type: ignore[arg-type]
    assert raised.value.reason == reason
    assert TEST_KEY_A not in str(raised.value) and TEST_KEY_B not in str(raised.value)


def test_key_ring_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY_RING_ENV, raising=False)
    with pytest.raises(KeyRingUnavailable):
        KeyRing.from_env()
    monkeypatch.setenv(KEY_RING_ENV, f"a:{TEST_KEY_A}")
    assert KeyRing.from_env().primary.key_id == "a"


def test_keys_and_secrets_never_appear_in_repr_or_errors() -> None:
    ring = KeyRing.parse(f"b:{TEST_KEY_B},a:{TEST_KEY_A}")
    rendered = repr(ring) + repr(ring.primary) + str(ring.keys)
    for key in (TEST_KEY_A, TEST_KEY_B):
        assert key not in rendered
        assert repr(base64.b64decode(key)) not in rendered
    token = encrypt_secret(ring, "smtp_password", SECRET)
    with pytest.raises(SecretDecryptionError) as raised:
        decrypt_secret(RING_A, "telegram_bot_token", token)
    assert SECRET not in str(raised.value) and token not in str(raised.value)
