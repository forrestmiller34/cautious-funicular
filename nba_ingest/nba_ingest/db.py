"""Database utilities for connecting to PostgreSQL using SQLAlchemy."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_db_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine for the configured database URL."""
    engine = create_engine(database_url, future=True)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to the given engine."""
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def get_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = ["create_db_engine", "create_session_factory", "get_session"]
