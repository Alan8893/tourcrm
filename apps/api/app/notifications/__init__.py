"""Notification Center (ADR-0045).

- app.notifications.vocabulary — closed channel/status vocabularies and
  the `notification.delivery` outbox contract;
- app.notifications.repository — idempotent persistence contracts (#318);
- app.notifications.ports — Admin Policy / recipient access / preference
  policy ports (#319);
- app.notifications.engine — the Notification Engine application boundary
  business modules call instead of any provider (#319);
- app.notifications.delivery — the `notification.delivery` worker handler
  and the ChannelAdapter boundary (#325);
- app.notifications.email_adapter / app.notifications.smtp — the Email
  channel adapter and its SMTP transport (#327);
- app.notifications.telegram_adapter — the Telegram channel adapter over
  app.telegram.bot_api (#329, ADR-0047 §5);
- app.notifications.rendering — safe Telegram HTML rendering of templates
  from a Notification's render context (#336, ADR-0049 §2.5);
- app.notifications.catalog — the ten v1 business catalog keys and their
  specification-gate data (#336, ADR-0049 §2.8);
- app.notifications.preferences — personal master switch + per-event
  preference policies, linked-identity reachability (#336, ADR-0049 §2.1);
- app.notifications.business — the single boundary domain services call to
  plan a catalog notification inside their transaction (#336, ADR-0049).
"""
