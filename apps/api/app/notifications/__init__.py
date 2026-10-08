"""Notification Center (ADR-0045).

- app.notifications.vocabulary — closed channel/status vocabularies and
  the `notification.delivery` outbox contract;
- app.notifications.repository — idempotent persistence contracts (#318);
- app.notifications.ports — Admin Policy / recipient access / preference
  policy ports (#319);
- app.notifications.engine — the Notification Engine application boundary
  business modules call instead of any provider (#319).

Channel adapters and the worker are separate Issues.
"""
