"""Unit tests for the v1 business notification catalog in code and the
personal preference policies (Issue #336, ADR-0049 §2.1/§2.8; PO decisions
of Issue #336). Pure — no database."""

import itertools

import pytest

from app.notifications.catalog import (
    CATALOG,
    STATUS_BLOCKED,
    STATUS_PENDING,
    mandatory_event_types,
    personal_preference_event_types,
)
from app.notifications.preferences import MandatoryPreferencePolicy, PersonalOptInPreferencePolicy

APPROVED_KEYS = {
    "event.created",
    "event.updated",
    "event.cancelled",
    "event.rescheduled",
    "registration.created",
    "registration.cancelled",
    "attendance.changed",
    "news.published",
    "achievement.awarded",
    "membership.approved",
}


def test_catalog_contains_exactly_the_ten_approved_keys() -> None:
    assert set(CATALOG) == APPROVED_KEYS


def test_only_cancellation_and_reschedule_are_mandatory() -> None:
    assert set(mandatory_event_types()) == {"event.cancelled", "event.rescheduled"}


def test_group_routing_is_allowed_only_for_the_four_approved_keys() -> None:
    assert {key for key, entry in CATALOG.items() if entry.group_routing} == {
        "event.created",
        "event.cancelled",
        "event.rescheduled",
        "news.published",
    }


def test_membership_approved_is_blocked_and_everything_else_awaits_its_slice() -> None:
    assert {key for key, e in CATALOG.items() if e.status == STATUS_BLOCKED} == {
        "membership.approved"
    }
    assert all(
        e.status == STATUS_PENDING for key, e in CATALOG.items() if key != "membership.approved"
    )


def test_every_template_is_telegram_and_membership_has_no_link() -> None:
    for entry in CATALOG.values():
        assert entry.template_code.endswith(".telegram")
    assert CATALOG["membership.approved"].variables.links == {}


def test_personal_preferences_exclude_mandatory_and_blocked_keys() -> None:
    assert set(personal_preference_event_types()) == APPROVED_KEYS - {
        "event.cancelled",
        "event.rescheduled",
        "membership.approved",
    }


_VALUES = (None, False, True)


@pytest.mark.parametrize(("stored", "master"), list(itertools.product(_VALUES, _VALUES)))
def test_optional_events_need_both_master_and_event_on(
    stored: bool | None, master: bool | None
) -> None:
    allowed = PersonalOptInPreferencePolicy().allows(
        channel="telegram", stored_enabled=stored, master_enabled=master
    )
    assert allowed is (stored is True and master is True)


@pytest.mark.parametrize(("stored", "master"), list(itertools.product(_VALUES, _VALUES)))
def test_mandatory_events_ignore_the_personal_opt_out(
    stored: bool | None, master: bool | None
) -> None:
    assert MandatoryPreferencePolicy().allows(
        channel="telegram", stored_enabled=stored, master_enabled=master
    )
