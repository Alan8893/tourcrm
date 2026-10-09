"""Interpretation of one trusted Telegram Bot API update (Issue #329,
ADR-0047 §4.1/§4.3).

`handle_update` is the transport-independent entry point the poller (and a
future webhook transport, ADR-0047 §7) calls inside the transaction that
also records the update as processed. It only acts on a `message` whose
text is `/start` or `/start <parameter>` sent in a **private** chat by a
non-bot user whose id equals the chat id; everything else — group and
channel messages, other commands, edits, malformed payloads — is ignored
without a reply, so a group message can never link an account.

It returns the reply to send *after* the transaction commits. Replies are
fixed texts: they never echo the token or anything else from the message,
and every rejected token gets the same answer. The message text is never
logged or stored.
"""

from dataclasses import dataclass
from typing import Any, Literal, Optional

from sqlalchemy.orm import Session

from app.telegram.linking import LINK_REJECTED, consume_link_challenge

REPLY_LINKED = (
    "Telegram-аккаунт привязан к TourCRM. Уведомления TourCRM будут приходить в этот чат."
)
REPLY_REJECTED = (
    "Не удалось привязать Telegram-аккаунт: ссылка недействительна или устарела. "
    "Запросите новую ссылку привязки в TourCRM."
)
REPLY_START_WITHOUT_LINK = (
    "Чтобы привязать Telegram-аккаунт, откройте ссылку привязки, полученную в TourCRM."
)

UpdateOutcome = Literal["ignored", "start_without_link", "linked", "already_linked", "rejected"]


@dataclass(frozen=True)
class UpdateResult:
    """`reply_chat_id`/`reply_text` are None when no reply is due."""

    outcome: UpdateOutcome
    reply_chat_id: Optional[int] = None
    reply_text: Optional[str] = None


_IGNORED = UpdateResult("ignored")


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _start_parameter(text: str) -> Optional[str]:
    """`""` for a bare `/start`, the parameter for `/start <parameter>`,
    None when the text is not a /start command."""
    command, _, rest = text.partition(" ")
    if command != "/start":
        return None
    return rest.strip()


def handle_update(session: Session, update: dict[str, Any]) -> UpdateResult:
    message = update.get("message")
    if not isinstance(message, dict):
        return _IGNORED
    chat = message.get("chat")
    sender = message.get("from")
    text = message.get("text")
    if not isinstance(chat, dict) or not isinstance(sender, dict) or not isinstance(text, str):
        return _IGNORED
    if chat.get("type") != "private" or sender.get("is_bot") is not False:
        return _IGNORED
    telegram_user_id = _positive_int(sender.get("id"))
    if telegram_user_id is None or _positive_int(chat.get("id")) != telegram_user_id:
        return _IGNORED
    parameter = _start_parameter(text)
    if parameter is None:
        return _IGNORED
    if not parameter:
        return UpdateResult("start_without_link", telegram_user_id, REPLY_START_WITHOUT_LINK)

    outcome = consume_link_challenge(
        session, raw_token=parameter, telegram_user_id=telegram_user_id
    )
    if outcome == LINK_REJECTED:
        return UpdateResult("rejected", telegram_user_id, REPLY_REJECTED)
    return UpdateResult(outcome, telegram_user_id, REPLY_LINKED)


__all__ = [
    "REPLY_LINKED",
    "REPLY_REJECTED",
    "REPLY_START_WITHOUT_LINK",
    "UpdateOutcome",
    "UpdateResult",
    "handle_update",
]
