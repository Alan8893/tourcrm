from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base and metadata for all ORM models.

    Domain models (added by their own Issues) map onto this Base so Alembic
    autogenerate can see the full schema through a single metadata object.
    """
