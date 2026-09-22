"""Pure-Python unit tests for the derived EventDocumentRequirement check
result (TH-0117.4 / Issue #162; ADR-0040 §5) — no database, no HTTP.
"""

import datetime as dt

from app.documents.validity import document_check_result


def _utc(*args: int) -> dt.datetime:
    return dt.datetime(*args, tzinfo=dt.timezone.utc)


def test_active_with_future_expiry_is_valid() -> None:
    result = document_check_result(
        status="active", expires_at=_utc(2030, 1, 1), now=_utc(2026, 1, 1)
    )
    assert result == "valid"


def test_active_with_no_expiry_is_valid() -> None:
    result = document_check_result(status="active", expires_at=None, now=_utc(2026, 1, 1))
    assert result == "valid"


def test_active_with_past_expiry_is_expired() -> None:
    result = document_check_result(
        status="active", expires_at=_utc(2020, 1, 1), now=_utc(2026, 1, 1)
    )
    assert result == "expired"


def test_stored_expired_status_is_expired() -> None:
    # Even if expires_at is somehow still in the future (e.g. an
    # administrative correction) — an explicitly stored `expired` status
    # is never re-derived back to `valid` at read time.
    result = document_check_result(
        status="expired", expires_at=_utc(2030, 1, 1), now=_utc(2026, 1, 1)
    )
    assert result == "expired"


def test_revoked_status_maps_to_expired_check_result() -> None:
    # ADR-0040 §5's revoked-mapping amendment: the persisted status stays
    # `revoked` elsewhere (this function never writes anything) but the
    # derived requirement-check result is `expired`, never a fourth
    # `revoked` value.
    result = document_check_result(
        status="revoked", expires_at=_utc(2030, 1, 1), now=_utc(2026, 1, 1)
    )
    assert result == "expired"


def test_revoked_status_with_past_expiry_is_still_expired_not_something_else() -> None:
    result = document_check_result(
        status="revoked", expires_at=_utc(2020, 1, 1), now=_utc(2026, 1, 1)
    )
    assert result == "expired"


def test_revoked_status_with_no_expiry_is_still_expired() -> None:
    result = document_check_result(status="revoked", expires_at=None, now=_utc(2026, 1, 1))
    assert result == "expired"


def test_expiry_exactly_at_now_is_expired() -> None:
    # Boundary behaviour: the exact expiry instant counts as already
    # elapsed (`<=`, mirroring app.people.guardian_lifecycle.
    # effective_status's identical boundary convention) — not "still valid
    # for one more instant".
    now = _utc(2026, 1, 1)
    result = document_check_result(status="active", expires_at=now, now=now)
    assert result == "expired"


def test_expiry_one_microsecond_in_the_future_is_still_valid() -> None:
    now = _utc(2026, 1, 1)
    expires_at = now + dt.timedelta(microseconds=1)
    result = document_check_result(status="active", expires_at=expires_at, now=now)
    assert result == "valid"


def test_now_defaults_to_the_real_current_time_when_omitted() -> None:
    far_future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3650)
    assert document_check_result(status="active", expires_at=far_future) == "valid"

    far_past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3650)
    assert document_check_result(status="active", expires_at=far_past) == "expired"
