from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from kubesage.config import get_settings
from kubesage.db.models import Base

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


# create_all only creates missing tables; columns added to existing tables after Phase 2 are added here.
COLUMN_MIGRATIONS = [
    "ALTER TABLE pod_startups ADD COLUMN IF NOT EXISTS pod_snapshot JSONB",
    "ALTER TABLE pod_startups ADD COLUMN IF NOT EXISTS logs TEXT",
]


def init_db() -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for statement in COLUMN_MIGRATIONS:
            connection.execute(text(statement))


@contextmanager
def session_scope() -> Iterator[Session]:
    """Session for background threads: commits on success, rolls back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with SessionLocal() as session:
        yield session
