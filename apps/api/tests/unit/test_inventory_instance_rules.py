"""Inventory Slice 2 pure instance rules (Issue #230;
docs/04-domain/inventory.md §7.4, §7.5, §16) — no database."""

import pytest

from app.inventory.lifecycle import (
    INSTANCE_TRANSITIONS,
    InstanceWrittenOffError,
    InvalidInstanceTransitionError,
    InvalidInventoryDataError,
    ItemNotInstanceModeError,
    ensure_instance_editable,
    ensure_instance_mode,
    format_inventory_number,
    next_instance_state,
    normalize_optional_text,
    normalize_write_off_reason,
)
from app.inventory.vocabulary import (
    CANONICAL_INSTANCE_STATES,
    CANONICAL_MOVEMENT_TYPES,
    INSTANCE_STATES_IN_STORAGE,
)

ALLOWED = [
    ("available", "transfer", "available"),
    ("in_repair", "transfer", "in_repair"),
    ("available", "repair_start", "in_repair"),
    ("in_repair", "repair_end", "available"),
    ("available", "write_off", "written_off"),
    ("in_repair", "write_off", "written_off"),
    ("written_off", "writeoff_reversal", "available"),
]


def test_vocabularies() -> None:
    assert CANONICAL_INSTANCE_STATES == {"available", "issued", "in_repair", "written_off"}
    assert INSTANCE_STATES_IN_STORAGE == {"available", "in_repair"}
    assert {"repair_start", "repair_end", "writeoff_reversal"} <= CANONICAL_MOVEMENT_TYPES


@pytest.mark.parametrize(("state", "movement", "expected"), ALLOWED)
def test_allowed_transitions(state: str, movement: str, expected: str) -> None:
    assert next_instance_state(state, movement) == expected


@pytest.mark.parametrize(
    ("state", "movement"),
    [
        (state, movement)
        for movement in INSTANCE_TRANSITIONS
        for state in sorted(CANONICAL_INSTANCE_STATES)
        if (state, movement) not in {(s, m) for s, m, _ in ALLOWED}
    ],
)
def test_every_other_transition_is_rejected(state: str, movement: str) -> None:
    with pytest.raises(InvalidInstanceTransitionError):
        next_instance_state(state, movement)


def test_issued_is_not_reachable_or_leavable_in_slice_2() -> None:
    for movement in INSTANCE_TRANSITIONS:
        with pytest.raises(InvalidInstanceTransitionError):
            next_instance_state("issued", movement)
    targets = {target for _, target in INSTANCE_TRANSITIONS.values()}
    assert "issued" not in targets


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [(1, "INV-000001"), (123, "INV-000123"), (999_999, "INV-999999"), (1_000_000, "INV-1000000")],
)
def test_inventory_number_format(sequence: int, expected: str) -> None:
    assert format_inventory_number(sequence) == expected


def test_inventory_number_starts_at_one() -> None:
    with pytest.raises(ValueError):
        format_inventory_number(0)


def test_written_off_instance_is_read_only() -> None:
    for state in ("available", "in_repair", "issued"):
        ensure_instance_editable(state)
    with pytest.raises(InstanceWrittenOffError):
        ensure_instance_editable("written_off")


def test_only_instance_mode_items_have_instances() -> None:
    ensure_instance_mode("instance")
    with pytest.raises(ItemNotInstanceModeError):
        ensure_instance_mode("quantity")


def test_optional_text_is_trimmed_and_blank_clears() -> None:
    assert normalize_optional_text("  4607 ", max_length=10, field="x") == "4607"
    assert normalize_optional_text("   ", max_length=10, field="x") is None
    assert normalize_optional_text(None, max_length=10, field="x") is None
    with pytest.raises(InvalidInventoryDataError):
        normalize_optional_text("x" * 11, max_length=10, field="x")


def test_write_off_reason_is_required() -> None:
    assert normalize_write_off_reason("  утерян в походе ", max_length=100) == "утерян в походе"
    for blank in ("", "   "):
        with pytest.raises(InvalidInventoryDataError):
            normalize_write_off_reason(blank, max_length=100)
