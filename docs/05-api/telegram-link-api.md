# Telegram Link API

- **Status:** Implemented (Issue #329)
- **Architecture:** ADR-0047 (Telegram long polling and linking), ADR-0045 (Notification Center)
- **Code:** `apps/api/app/api/v1/telegram_link.py`, `apps/api/app/telegram/linking.py`

The authenticated User links **their own** Telegram account to TourCRM so the Telegram channel can deliver private notifications to them. The flow is user-driven: Telegram does not let a bot start a private chat, so the User opens a one-time deep link and presses **Start** in the bot.

All endpoints are self-scoped (like `/auth/sessions`): the identity is the session-derived principal; no RBAC permission is involved and no request field can name another User. **No endpoint accepts a Telegram id.** The Telegram identity is established only when the bot receives `/start <token>` from that Telegram account in a private chat (trusted Bot API update, received by the separate poller process — ADR-0047 §3).

## `GET /api/v1/me/telegram-link`

Current link status.

```json
{ "linked": true, "linked_at": "2026-10-09T10:00:00Z" }
```

`linked_at` is `null` when not linked. The Telegram user id is never returned.

Errors: `401` unauthenticated.

## `POST /api/v1/me/telegram-link/challenges`

Issues a one-time linking challenge. Requires the CSRF header (`X-CSRF-Token`). No request body is read.

`201 Created`, `Cache-Control: no-store`:

```json
{
  "deep_link": "https://t.me/<bot_username>?start=<one-time token>",
  "expires_at": "2026-10-09T10:15:00Z"
}
```

- The raw token appears only in this response, inside `deep_link`. It is a 43-character URL-safe random value (256 bits), within Telegram's deep-link `start` limit (1–64 characters of `A–Z a–z 0–9 _ -`). Only its SHA-256 hash is stored.
- Lifetime: 15 minutes (authoritative database time). Single use.
- Issuing a new challenge revokes the User's previous pending one (at most one pending challenge per User).
- Rate limit: at most 5 challenges per User per rolling hour, counted in PostgreSQL under a lock on the User row.

Errors:

| Status | `error.code` | When |
|---|---|---|
| 401 | `unauthorized` | not authenticated |
| 403 | `forbidden` | missing/invalid CSRF token, or the account is not active |
| 429 | `too_many_requests` | issuance rate limit exceeded |
| 503 | `telegram_linking_unavailable` | the Telegram bot username is not configured in Settings → Notifications (ADR-0048) |

## `DELETE /api/v1/me/telegram-link`

Unlinks the active Telegram identity (lifecycle `unlinked`, history kept) and revokes any pending challenge. Requires the CSRF header. Idempotent: `204 No Content` whether or not a link existed.

## Bot side (`/start <token>`)

Handled by `app.telegram.updates.handle_update` inside the poller's per-update transaction:

| Situation | Result | Bot reply |
|---|---|---|
| valid pending token, sender not linked anywhere | identity linked; the User's previous identity (if any) ends as `replaced` | success text |
| valid token, sender already is this User's identity | confirmed, nothing changes | success text |
| valid token, sender actively linked to **another** User | rejected; never transferred; challenge stays pending | generic failure |
| unknown, expired, consumed, revoked or malformed token; inactive owner | rejected | generic failure (identical for all cases) |
| bare `/start` | — | instruction to open the link from TourCRM |
| group/supergroup/channel message, bot sender, any other text | ignored | none |

Replies never echo the token or message content. To move a Telegram account from User A to User B, A must unlink it first (`DELETE` above); then B links it with a fresh challenge.

Audit (ADR-0024 as amended by ADR-0047 §4.3): `telegram_link_challenge.created`, `telegram_identity.linked` (success, or failure with `details.reason = identity_linked_to_another_user`), `telegram_identity.unlinked` (`details.reason` = `user_unlinked` | `replaced`). Metadata only — never tokens, hashes, Telegram ids or messages.
