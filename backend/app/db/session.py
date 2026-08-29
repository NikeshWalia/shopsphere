"""Engine and session lifecycle."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    # Recycles connections killed by the server or a NAT idle timeout; without
    # it the first request after an idle period fails with a stale connection.
    pool_pre_ping=settings.db_pool_pre_ping,
    echo=settings.db_echo,
    future=True,
    connect_args={
        "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
        "application_name": "shopsphere-api",
    },
)

# expire_on_commit is left at SQLAlchemy's default of True on purpose.
#
# Turning it off looks like a free optimisation, but it means an object loaded
# before a commit keeps serving pre-commit state afterwards - including stale
# relationship collections. That produced a real defect here: adding the first
# item to a new cart returned an empty cart, because the Cart instance still
# held the empty `items` collection it was created with. Correctness first; the
# extra reload after a commit is one indexed query.
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session.

    The session is *not* committed here. Endpoints and services own their
    transaction boundaries explicitly, which keeps multi-step operations such
    as checkout atomic instead of accidentally committing halfway through.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session for CLI entry points (seeding, scripts)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
