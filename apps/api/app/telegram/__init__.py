"""Telegram integration boundary (Issue #329, ADR-0047).

- app.telegram.vocabulary — identity/challenge lifecycle vocabularies,
  challenge TTL/rate limit and the deep-link start-parameter alphabet;
- app.telegram.bot_api — the only module that talks HTTP to the Telegram
  Bot API (`getMe`, `getUpdates`, `sendMessage`); turns transport and
  provider failures into stable, secret-free values;
- app.telegram.linking — the linking application service: one-time
  challenges and the User <-> Telegram identity lifecycle (link, replace,
  unlink). Transport-agnostic: it never calls the Bot API;
- app.telegram.updates — interprets one trusted Bot API update
  (`/start <token>` in a private chat) and calls the linking service;
- app.telegram.poller — the dedicated long-polling runtime with the
  durable PostgreSQL update checkpoint, run by app.cli.run_telegram_poller.

The outbound Telegram channel adapter lives with the other channel
adapters in app.notifications.telegram_adapter.
"""
