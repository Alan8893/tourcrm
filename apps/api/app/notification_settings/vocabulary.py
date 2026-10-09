"""Closed vocabularies of Administrator Notification Settings (Issue #333,
ADR-0048 §2.6–§2.10). Pure Python — app.db.notification_settings builds
its CHECK constraints from these sets."""

from datetime import timedelta

# UI-managed integration secrets (ADR-0048 §2.3). The identifier is also the
# AES-GCM associated data, binding each ciphertext to its own slot.
SECRET_SMTP_PASSWORD = "smtp_password"
SECRET_TELEGRAM_BOT_TOKEN = "telegram_bot_token"

CANONICAL_SECRET_NAMES: frozenset[str] = frozenset(
    {SECRET_SMTP_PASSWORD, SECRET_TELEGRAM_BOT_TOKEN}
)

# SMTP connection security modes (mirrors app.core.config.SMTP_SECURITY_MODES).
SMTP_SECURITY_STARTTLS = "starttls"
SMTP_SECURITY_SSL = "ssl"
SMTP_SECURITY_NONE = "none"
CANONICAL_SMTP_SECURITY_MODES: frozenset[str] = frozenset(
    {SMTP_SECURITY_STARTTLS, SMTP_SECURITY_SSL, SMTP_SECURITY_NONE}
)
SMTP_DEFAULT_PORTS = {SMTP_SECURITY_STARTTLS: 587, SMTP_SECURITY_SSL: 465, SMTP_SECURITY_NONE: 25}
# Connect/command timeout of one SMTP conversation (not configurable).
SMTP_TIMEOUT_SECONDS = 30.0

# Channel configuration state, as reported by readiness and used by the
# runtime: only `configured` can be used for sending.
CONFIG_NOT_CONFIGURED = "not_configured"
CONFIG_INCOMPLETE = "incomplete"
CONFIG_INVALID = "invalid"
CONFIG_SECRET_UNAVAILABLE = "secret_unavailable"
CONFIG_CONFIGURED = "configured"

# Encryption key ring status.
ENCRYPTION_AVAILABLE = "available"
ENCRYPTION_MISSING = "missing"
ENCRYPTION_INVALID = "invalid"

# Test send (ADR-0048 §2.10).
TEST_SEND_RATE_LIMIT_COUNT = 5
TEST_SEND_RATE_LIMIT_WINDOW = timedelta(minutes=10)
TEST_DESTINATION_EMAIL = "email_address"
TEST_DESTINATION_TELEGRAM_SELF = "own_telegram_account"
TEST_DESTINATION_TELEGRAM_DESTINATION = "telegram_destination"
CANONICAL_TEST_DESTINATION_KINDS: frozenset[str] = frozenset(
    {TEST_DESTINATION_EMAIL, TEST_DESTINATION_TELEGRAM_SELF, TEST_DESTINATION_TELEGRAM_DESTINATION}
)
TEST_OUTCOME_DELIVERED = "delivered"
TEST_OUTCOME_FAILED = "failed"
CANONICAL_TEST_OUTCOMES: frozenset[str] = frozenset({TEST_OUTCOME_DELIVERED, TEST_OUTCOME_FAILED})

# The installation-wide singleton rows.
SINGLETON_ID = 1
