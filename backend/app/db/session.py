"""Engine, session factory, and the cross-dialect column types.

Postgres is the deployment target. SQLite is what the test suite runs on, so
that a clone with no database container can still execute the API integration
tests. The two variants declared below are the only places the difference leaks.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import JSON, String, create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    """Client-side timestamp default with microsecond precision.

    SQLite's CURRENT_TIMESTAMP has one-second resolution, which is not enough
    to order two rows written inside the same simulation step. ``server_default``
    is still declared on the models so rows inserted outside the ORM get stamped.
    """
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


JsonBlob = JSON().with_variant(JSONB(), "postgresql")
# Postgres has a native UUID type and the schema in docs/architecture.md uses
# it. SQLite does not, so ids fall back to a 36-char string there. Application
# code treats an id as an opaque ``str`` in both cases.
UUIDStr = String(36).with_variant(PgUUID(as_uuid=False), "postgresql")


_settings = get_settings()
_connect_args = (
    {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    future=True,
    connect_args=_connect_args,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
