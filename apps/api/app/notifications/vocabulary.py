"""Canonical Notification Center vocabularies (Issue #318).

Pure Python — no ORM/FastAPI import, mirroring app.news.vocabulary:
app.db.notifications builds its CHECK constraints from these sets and
app.notifications.repository validates input against them.

Canonical sources: ADR-0045 §2.5 (MVP channels),
docs/04-modules/notifications-and-communications.md §4 (Delivery
statuses), docs/03-architecture/database-schema.md §18.
"""

# ADR-0045 §2.5: Email and Telegram are the MVP channels. MAX is an
# architectural extension point only — adding it is a vocabulary +
# CHECK-constraint migration, not a schema redesign.
CHANNEL_EMAIL = "email"
CHANNEL_TELEGRAM = "telegram"

CANONICAL_NOTIFICATION_CHANNELS: frozenset[str] = frozenset({CHANNEL_EMAIL, CHANNEL_TELEGRAM})

# Logical Notification lifecycle (database-schema.md §18). Channel outcome
# (delivered/failed) is Delivery state and is never duplicated here:
# - pending   — created; Deliveries not yet materialized;
# - processed — the Notification Engine has materialized its Deliveries;
# - skipped   — no Delivery is created because effective policy suppresses
#               every channel;
# - cancelled — withdrawn before processing.
NOTIFICATION_PENDING = "pending"
NOTIFICATION_PROCESSED = "processed"
NOTIFICATION_SKIPPED = "skipped"
NOTIFICATION_CANCELLED = "cancelled"

CANONICAL_NOTIFICATION_STATUSES: frozenset[str] = frozenset(
    {NOTIFICATION_PENDING, NOTIFICATION_PROCESSED, NOTIFICATION_SKIPPED, NOTIFICATION_CANCELLED}
)

# notifications-and-communications.md §4: the closed Delivery status
# vocabulary.
DELIVERY_PENDING = "pending"
DELIVERY_PROCESSING = "processing"
DELIVERY_DELIVERED = "delivered"
DELIVERY_FAILED = "failed"
DELIVERY_CANCELLED = "cancelled"
DELIVERY_SKIPPED = "skipped"

CANONICAL_DELIVERY_STATUSES: frozenset[str] = frozenset(
    {
        DELIVERY_PENDING,
        DELIVERY_PROCESSING,
        DELIVERY_DELIVERED,
        DELIVERY_FAILED,
        DELIVERY_CANCELLED,
        DELIVERY_SKIPPED,
    }
)

# What `notification_deliveries.destination_id` refers to. A Delivery
# stores an internal identifier, never a raw email address or Telegram
# chat id (notifications-and-communications.md §8 "внутренний
# идентификатор destination"):
# - user                 — the recipient User's own channel identity (their
#                          email / linked Telegram identity), resolved by
#                          the channel adapter at send time;
# - telegram_destination — a `telegram_destinations` row (group / topic).
DESTINATION_USER = "user"
DESTINATION_TELEGRAM_DESTINATION = "telegram_destination"

CANONICAL_DESTINATION_TYPES: frozenset[str] = frozenset(
    {DESTINATION_USER, DESTINATION_TELEGRAM_DESTINATION}
)

__all__ = [
    "CHANNEL_EMAIL",
    "CHANNEL_TELEGRAM",
    "CANONICAL_NOTIFICATION_CHANNELS",
    "NOTIFICATION_PENDING",
    "NOTIFICATION_PROCESSED",
    "NOTIFICATION_SKIPPED",
    "NOTIFICATION_CANCELLED",
    "CANONICAL_NOTIFICATION_STATUSES",
    "DELIVERY_PENDING",
    "DELIVERY_PROCESSING",
    "DELIVERY_DELIVERED",
    "DELIVERY_FAILED",
    "DELIVERY_CANCELLED",
    "DELIVERY_SKIPPED",
    "CANONICAL_DELIVERY_STATUSES",
    "DESTINATION_USER",
    "DESTINATION_TELEGRAM_DESTINATION",
    "CANONICAL_DESTINATION_TYPES",
]
