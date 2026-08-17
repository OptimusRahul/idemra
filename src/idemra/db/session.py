"""Database engine/session — single Postgres source of truth.

Connection string follows the same IDEMRA_DATABASE_URL convention as
migrations/env.py, so CLI commands and Alembic always point at the same
database without separate configuration.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import typer
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "postgresql+psycopg://idemra:idemra@localhost:5433/idemra"


def get_engine() -> Engine:
    url = os.environ.get("IDEMRA_DATABASE_URL", DEFAULT_DATABASE_URL)
    return create_engine(url)


@contextmanager
def session_scope() -> Iterator[Session]:
    """One engine/session per call.

    Reads IDEMRA_DATABASE_URL fresh each time rather than caching a global
    engine, so tests can point at a different database per test (via
    monkeypatch) without a stale connection leaking across them.

    `typer.Exit` is CLI control flow (a command signaling its exit code
    after finishing its work), not a transaction failure — it subclasses
    Exception (via RuntimeError), so a bare `except Exception` would
    silently roll back every write a command made before choosing to exit
    non-zero. Every `fail_run(...)` followed by `raise typer.Exit(code=1)`
    depends on this distinction to actually persist.
    """
    engine = get_engine()
    session = sessionmaker(bind=engine)()
    try:
        yield session
        session.commit()
    except typer.Exit:
        session.commit()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()
