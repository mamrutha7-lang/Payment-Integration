"""SQLAlchemy engine, session factory, and the declarative Base.

The fresh Python schema is defined on this Base (see app/models). Alembic will
own migrations; `Base.metadata.create_all` is used only by the dev seed so the
app can run before the migration tooling is wired.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


# Pool sized from settings, never SQLAlchemy's silent defaults (5+10, 30 s
# wait): those made 15 connections the whole app's ceiling — every sync endpoint
# holds one for its full handler — with nothing but a code edit to raise it.
# The knobs live in Settings so a deployment can size workers x pool against
# Postgres max_connections without touching this file (see config.py for the
# reasoning and docker-compose.prod.yml for the production arithmetic).
engine = create_engine(
    settings.sqlalchemy_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout_s,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
