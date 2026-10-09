"""The Global Admin Policy as the Notification Engine's policy source
(Issue #333, ADR-0045 §2.4/§2.10, ADR-0048 §2.1/§2.8).

`GlobalAdminPolicy` implements app.notifications.ports.AdminPolicy from the
persisted installation-wide policy: no saved policy means every channel is
OFF, so the Engine stays fail closed. TourCRM has no Club Admin Policy
(ADR-0048 §2.1), so the club level never adds a restriction of its own —
the global level, which the Engine checks first, is the only one.
"""

import uuid

from sqlalchemy.orm import Session

from app.notification_settings.runtime import global_channel_enabled


class GlobalAdminPolicy:
    def global_channel_enabled(self, session: Session, *, channel: str) -> bool:
        return global_channel_enabled(session, channel)

    def club_channel_enabled(self, session: Session, *, club_id: uuid.UUID, channel: str) -> bool:
        return True


__all__ = ["GlobalAdminPolicy"]
