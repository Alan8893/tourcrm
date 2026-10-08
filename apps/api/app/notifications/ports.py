"""Policy and audience ports of the Notification Engine (Issue #319,
ADR-0045 §2.4/§2.10, §4).

The Engine executes notification policy; it never invents it. Every
decision that canonical docs leave to another slice reaches the Engine
through one of these ports:

- `AdminPolicy` — the effective Global and Club Admin Policy (feature
  policy, docs/09-governance/feature-settings.md §10). Its persistence/UI
  is the Administrator Notification Settings slice; until a source is
  connected the Engine is called with `admin_policy=None` and fails closed.
- `RecipientAccess` — backend resource authorization of each candidate
  recipient. The concrete check per business event comes from that
  event's specification gate; `PermissionRecipientAccess` adapts the
  existing RBAC + scope engine (app.authorization.service.can) so no new
  authorization mechanism, permission or scope is introduced.
- `PreferencePolicy` — the event's effective User Preference policy
  (mandatory / opt-in / opt-out, including what a missing stored
  preference means), defined by the specification gate (ADR-0045 §2.10).
  The Engine has no built-in preference default.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.authorization.context import ResourceContext
from app.authorization.service import can


class AdminPolicy(Protocol):
    """Effective administrator feature policy per channel. Each method
    answers only its own level; the Engine applies the hierarchy (Global
    before Club, restrict-only)."""

    def global_channel_enabled(self, session: Session, *, channel: str) -> bool: ...

    def club_channel_enabled(
        self, session: Session, *, club_id: uuid.UUID, channel: str
    ) -> bool: ...


class RecipientAccess(Protocol):
    """Whether `user_id` is authorized to see the notification's business
    resource/context. A recipient is never trusted because its id was
    supplied."""

    def allows(self, session: Session, *, user_id: uuid.UUID) -> bool: ...


class PreferencePolicy(Protocol):
    """The event's effective User Preference policy for one channel.
    `stored_enabled` is the recipient's stored preference for
    (channel, event_type), or None when there is none."""

    def allows(self, *, channel: str, stored_enabled: bool | None) -> bool: ...


@dataclass(frozen=True)
class PermissionRecipientAccess:
    """RecipientAccess over the existing authorization engine: the recipient
    must hold `permission_code` for the ResourceContext that
    `context_for` resolves for them from trusted server-side data (e.g.
    app.events.authorization.build_event_resource_context)."""

    permission_code: str
    context_for: Callable[[Session, uuid.UUID], ResourceContext]

    def allows(self, session: Session, *, user_id: uuid.UUID) -> bool:
        return can(session, user_id, self.permission_code, self.context_for(session, user_id))


__all__ = ["AdminPolicy", "RecipientAccess", "PreferencePolicy", "PermissionRecipientAccess"]
