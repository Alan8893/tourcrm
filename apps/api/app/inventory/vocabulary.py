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

# inventory.md §13 п.2: the minimal canonical movement types.
MOVEMENT_RECEIPT = "receipt"
MOVEMENT_TRANSFER = "transfer"
MOVEMENT_ISSUE = "issue"
MOVEMENT_RETURN = "return"
MOVEMENT_WRITE_OFF = "write_off"
MOVEMENT_ADJUSTMENT = "adjustment"

CANONICAL_MOVEMENT_TYPES: frozenset[str] = frozenset(
    {
        MOVEMENT_RECEIPT,
        MOVEMENT_TRANSFER,
        MOVEMENT_ISSUE,
        MOVEMENT_RETURN,
        MOVEMENT_WRITE_OFF,
        MOVEMENT_ADJUSTMENT,
    }
)

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
    "CANONICAL_MOVEMENT_TYPES",
    "SYSTEM_UNIT_NAMES",
]
