"""Canonical Inventory vocabularies (docs/04-domain/inventory.md).

Pure Python — no ORM/FastAPI import, mirroring app.news.vocabulary:
app.db.inventory builds its CHECK constraints from these sets and
app.inventory.lifecycle validates against them.
"""

ACTIVE = "active"
ARCHIVED = "archived"

# inventory.md §17: `active -> archived`, irreversible. Shared by
# nomenclature, categories, units and storage locations.
CANONICAL_INVENTORY_STATUSES: frozenset[str] = frozenset({ACTIVE, ARCHIVED})

ACCOUNTING_MODE_QUANTITY = "quantity"
ACCOUNTING_MODE_INSTANCE = "instance"

# inventory.md §5: one nomenclature model + a per-item accounting mode.
CANONICAL_ACCOUNTING_MODES: frozenset[str] = frozenset(
    {ACCOUNTING_MODE_QUANTITY, ACCOUNTING_MODE_INSTANCE}
)

# inventory.md §13 п.2: the canonical movement types (Foundation set).
MOVEMENT_RECEIPT = "receipt"
MOVEMENT_TRANSFER = "transfer"
MOVEMENT_ISSUE = "issue"
MOVEMENT_RETURN = "return"
MOVEMENT_WRITE_OFF = "write_off"
MOVEMENT_ADJUSTMENT = "adjustment"
# PO decision E: the compensating movement of an erroneous write-off.
MOVEMENT_WRITEOFF_REVERSAL = "writeoff_reversal"
# inventory.md §7.3 (Q6, R1): repair of an instance.
MOVEMENT_REPAIR_START = "repair_start"
MOVEMENT_REPAIR_END = "repair_end"

CANONICAL_MOVEMENT_TYPES: frozenset[str] = frozenset(
    {
        MOVEMENT_RECEIPT,
        MOVEMENT_TRANSFER,
        MOVEMENT_ISSUE,
        MOVEMENT_RETURN,
        MOVEMENT_WRITE_OFF,
        MOVEMENT_ADJUSTMENT,
        MOVEMENT_WRITEOFF_REVERSAL,
        MOVEMENT_REPAIR_START,
        MOVEMENT_REPAIR_END,
    }
)

# Movements that only exist for instance-accounted items.
INSTANCE_ONLY_MOVEMENT_TYPES: frozenset[str] = frozenset(
    {MOVEMENT_REPAIR_START, MOVEMENT_REPAIR_END}
)

# inventory.md §7.2 (Q5): instance states.
INSTANCE_AVAILABLE = "available"
INSTANCE_ISSUED = "issued"
INSTANCE_IN_REPAIR = "in_repair"
INSTANCE_WRITTEN_OFF = "written_off"

CANONICAL_INSTANCE_STATES: frozenset[str] = frozenset(
    {INSTANCE_AVAILABLE, INSTANCE_ISSUED, INSTANCE_IN_REPAIR, INSTANCE_WRITTEN_OFF}
)
# inventory.md §7.2 п.5 (R3): states in which the instance is in a storage
# location; in every other state it has none.
INSTANCE_STATES_IN_STORAGE: frozenset[str] = frozenset({INSTANCE_AVAILABLE, INSTANCE_IN_REPAIR})

# inventory.md §9 п.2 (PO decision G11): seeded by migration, immutable.
SYSTEM_UNIT_NAMES: tuple[str, ...] = ("шт", "м", "комплект", "пара")

__all__ = [
    "ACTIVE",
    "ARCHIVED",
    "CANONICAL_INVENTORY_STATUSES",
    "ACCOUNTING_MODE_QUANTITY",
    "ACCOUNTING_MODE_INSTANCE",
    "CANONICAL_ACCOUNTING_MODES",
    "MOVEMENT_RECEIPT",
    "MOVEMENT_TRANSFER",
    "MOVEMENT_ISSUE",
    "MOVEMENT_RETURN",
    "MOVEMENT_WRITE_OFF",
    "MOVEMENT_ADJUSTMENT",
    "MOVEMENT_WRITEOFF_REVERSAL",
    "MOVEMENT_REPAIR_START",
    "MOVEMENT_REPAIR_END",
    "INSTANCE_ONLY_MOVEMENT_TYPES",
    "INSTANCE_AVAILABLE",
    "INSTANCE_ISSUED",
    "INSTANCE_IN_REPAIR",
    "INSTANCE_WRITTEN_OFF",
    "CANONICAL_INSTANCE_STATES",
    "INSTANCE_STATES_IN_STORAGE",
    "CANONICAL_MOVEMENT_TYPES",
    "SYSTEM_UNIT_NAMES",
]
