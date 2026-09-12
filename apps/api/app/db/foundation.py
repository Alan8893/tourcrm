"""Non-domain technical model used only to prove that Alembic migrations and
the SQLAlchemy ORM session work end-to-end (Issue #5).

`FoundationHealthCheck` carries no business meaning and must not be
referenced by domain modules; it exists solely so integration tests have a
real, migration-managed table to read/write without pre-committing to any
business entity.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FoundationHealthCheck(Base):
    __tablename__ = "foundation_healthchecks"

    # UUID primary key per docs/03-architecture/adr/ADR-0010-primary-key-strategy.md:
    # application-generated UUID4, stored as PostgreSQL's native UUID type.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
