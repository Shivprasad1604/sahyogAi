"""The database connection.

engine   = the phone line to the database
session  = one conversation over that line
get_db() = hand out a session, and ALWAYS close it afterwards

The try/finally matters: without it, sessions leak until the database
refuses new connections.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# check_same_thread is a SQLite-only quirk; harmless to guard for it.
_connect_args = {"check_same_thread": False} if settings.using_sqlite else {}

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,       # drop dead connections instead of erroring
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency. Yields a session, closes it no matter what."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create every table that doesn't exist yet."""
    from app import models  # noqa: F401  (registers the models on Base)

    Base.metadata.create_all(bind=engine)
