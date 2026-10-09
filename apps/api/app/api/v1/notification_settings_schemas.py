"""Request/response models for /api/v1/settings/notifications (Issue #333,
ADR-0048). No response model has a field for a secret value, a ciphertext
or an encryption key: secrets are reported only as `*_configured`
booleans. Secret request bodies carry the new value only and are never
echoed — validation messages name the field, never its input."""

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool

ChannelName = Literal["email", "telegram"]
ConfigurationState = Literal[
    "not_configured", "incomplete", "invalid", "secret_unavailable", "configured"
]
EncryptionState = Literal["available", "missing", "invalid"]
SmtpSecurity = Literal["starttls", "ssl", "none"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolicyOut(BaseModel):
    email_enabled: bool
    telegram_enabled: bool
    # False until an administrator saved the policy (all channels OFF).
    saved: bool


class PolicyIn(_Strict):
    email_enabled: StrictBool
    telegram_enabled: StrictBool


class RuleOut(BaseModel):
    id: uuid.UUID
    event_type: str
    channel: str
    recipient_scope: str
    is_enabled: bool


class RuleUpdateIn(_Strict):
    is_enabled: StrictBool


class ChannelStatusOut(BaseModel):
    policy_enabled: bool
    configuration: ConfigurationState
    # policy_enabled and configuration == "configured".
    ready: bool


class TelegramStatusOut(ChannelStatusOut):
    # A bot username is configured, so linking deep links can be issued.
    linking_available: bool


class NotificationStatusOut(BaseModel):
    encryption: EncryptionState
    email: ChannelStatusOut
    telegram: TelegramStatusOut


class TelegramDestinationOptionOut(BaseModel):
    id: uuid.UUID
    name: str
    topic_name: Optional[str]


class TestSendIn(_Strict):
    channel: ChannelName
    destination_kind: Literal["email_address", "own_telegram_account", "telegram_destination"]
    email: Optional[str] = Field(default=None, max_length=320)
    telegram_destination_id: Optional[uuid.UUID] = None


class TestSendOut(BaseModel):
    test: Literal[True] = True
    channel: ChannelName
    destination_kind: str
    status: Literal["delivered", "failed"]
    error_code: Optional[str]


class EmailSettingsOut(BaseModel):
    smtp_host: Optional[str]
    smtp_port: Optional[int]
    smtp_security: SmtpSecurity
    smtp_username: Optional[str]
    sender_email: Optional[str]
    sender_name: Optional[str]
    password_configured: bool


class EmailSettingsIn(_Strict):
    smtp_host: Optional[str] = Field(default=None, max_length=255)
    smtp_port: Optional[int] = Field(default=None, ge=1, le=65535)
    smtp_security: SmtpSecurity = "starttls"
    smtp_username: Optional[str] = Field(default=None, max_length=255)
    sender_email: Optional[str] = Field(default=None, max_length=320)
    sender_name: Optional[str] = Field(default=None, max_length=255)


class TelegramSettingsOut(BaseModel):
    bot_username: Optional[str]
    bot_token_configured: bool


class TelegramSettingsIn(_Strict):
    bot_username: Optional[str] = Field(default=None, max_length=64)


class IntegrationsOut(BaseModel):
    encryption: EncryptionState
    email: EmailSettingsOut
    telegram: TelegramSettingsOut


class SecretIn(_Strict):
    value: str = Field(min_length=1, max_length=1024)


class SecretStatusOut(BaseModel):
    configured: bool
