"""Request/response models for /api/v1/me/telegram-link (Issue #329,
ADR-0047 §4). No model carries a Telegram user id, a token hash or a
bot token; the raw linking token appears only inside `deep_link` of the
issuance response."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class TelegramLinkStatusOut(BaseModel):
    linked: bool
    linked_at: Optional[datetime]


class TelegramLinkChallengeOut(BaseModel):
    # https://t.me/<bot>?start=<one-time token> — open it and press Start.
    deep_link: str
    expires_at: datetime
