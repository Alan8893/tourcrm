"""Administrator Notification Settings (Issue #333, ADR-0048).

- app.notification_settings.vocabulary — secret identifiers, channel
  configuration states and test-send limits;
- app.notification_settings.crypto — the `SETTINGS_ENCRYPTION_KEYS` key
  ring and the AES-256-GCM secret token format (the only encryption code);
- app.notification_settings.store — persistence of the Global Admin
  Policy, the non-secret SMTP/Telegram settings and the encrypted secrets;
- app.notification_settings.runtime — the effective runtime configuration
  (SmtpSettings/TelegramSettings) and channel readiness, read by the API,
  the outbox worker and the Telegram poller for every use (no restart);
- app.notification_settings.policy — the Global Admin Policy source of the
  Notification Engine's AdminPolicy port;
- app.notification_settings.service — audited administrator operations;
- app.notification_settings.test_send — the synchronous, rate-limited test
  send through the existing channel adapters.
"""
