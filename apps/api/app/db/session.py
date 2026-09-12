"""Engine, session factory and per-request session boundary.

Deliberately no business logic and no module-level global session: the
engine/session-factory are built lazily (and cached) from environment
configuration so importing this module never requires DATABASE_URL to be
set, and every consumer gets its own Session from get_db()/session_scope().
"""

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.errors import DatabaseConnectionError


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True)


@lru_cache
def get_session_factory() -> sessionmaker:
    return sessionmaker(
        bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False
    )


def get_db() -> Generator[Session, None, None]:
    """FastAPI-compatible per-request session boundary.

    Usage (in a future endpoint): `db: Session = Depends(get_db)`. Yields one
    session for the lifetime of the request and always closes it afterwards.
    """
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Session boundary for non-request contexts (scripts, tests)."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def check_connection(database_url: str | None = None) -> None:
    """Verify database connectivity without leaking credentials on failure.

    Raises DatabaseConnectionError with a sanitized message; the original
    driver exception is not chained into the message so its `args` cannot
    surface a raw DSN through logs or API error payloads.
    """
    url_str = database_url or get_settings().database_url
    url = make_url(url_str)

    if database_url is not None:
        probe_engine = create_engine(url_str, pool_pre_ping=True)
    else:
        probe_engine = get_engine()

    try:
        with probe_engine.connect():
            pass
    except Exception as exc:
        raise DatabaseConnectionError(
            f"Could not connect to PostgreSQL at {url.host}:{url.port}/{url.database} "
            f"({type(exc).__name__})"
        ) from None
    finally:
        if database_url is not None:
            probe_engine.dispose()
