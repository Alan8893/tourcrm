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

# PROVISIONAL persistence vocabulary for `notifications.status`
# (database-schema.md §18). ADR-0045 does not yet define the logical
# Notification lifecycle; its business semantics and transitions are
# fixed by the Notification Engine (#319) / ADR-0045, which may change
# these values through a migration. Only `pending` — the initial value of
# a newly created Notification — is used by this foundation; no business
# logic may be built on the other values until #319 defines them.
# Channel outcome (delivered/failed) is Delivery state, not Notification
# state.
NOTIFICATION_PENDING = "pending"

PROVISIONAL_NOTIFICATION_STATUSES: frozenset[str] = frozenset(
    {NOTIFICATION_PENDING, "processed", "skipped", "cancelled"}
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

# ADR-0045 §2.10 outbox contract: exactly one job per Delivery.
NOTIFICATION_DELIVERY_JOB_TYPE = "notification.delivery"


def notification_delivery_deduplication_key(delivery_id: object) -> str:
    return f"notification_delivery:{delivery_id}"


__all__ = [
    "NOTIFICATION_DELIVERY_JOB_TYPE",
    "notification_delivery_deduplication_key",
    "CHANNEL_EMAIL",
    "CHANNEL_TELEGRAM",
    "CANONICAL_NOTIFICATION_CHANNELS",
    "NOTIFICATION_PENDING",
    "PROVISIONAL_NOTIFICATION_STATUSES",
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
