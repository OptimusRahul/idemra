"""Reliability tier fixtures — the one place in the suite that talks to
real Postgres and real Redis (everything under tests/unit/ deliberately
doesn't: SQLite substitutes for Postgres, and the project-wide conftest.py
autouse fixture points IDEMRA_REDIS_URL at an address nothing listens on).

These tests exist to verify the actual claim this project makes — crash
recovery and idempotency under real infrastructure failure modes, not just
against mocks. They need `docker compose -f docker/docker-compose.yml up -d`
running locally (or the postgres/redis service containers in CI) and skip
cleanly, rather than failing, when that infra isn't reachable — so a
contributor without Docker running can still get a green tests/unit run.

Uses db index 2 on the same Redis instance dev/CI Postgres/Redis use, kept
separate from whatever queue a human might have running locally in db 0.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import redis as redis_lib
import sqlalchemy
from sqlalchemy.orm import Session, sessionmaker

from idemra.db.models import Base

RELIABILITY_DATABASE_URL = "postgresql+psycopg://idemra:idemra@localhost:5433/idemra"
RELIABILITY_REDIS_URL = "redis://localhost:6379/2"


def _postgres_reachable() -> bool:
    try:
        engine = sqlalchemy.create_engine(RELIABILITY_DATABASE_URL)
        with engine.connect():
            return True
    except Exception:  # noqa: BLE001 — any connection failure means "skip", not "crash the run"
        return False
    finally:
        engine.dispose()


def _redis_reachable() -> bool:
    try:
        return redis_lib.Redis.from_url(RELIABILITY_REDIS_URL).ping()
    except Exception:  # noqa: BLE001 — any connection failure means "skip", not "crash the run"
        return False


@pytest.fixture(autouse=True)
def _real_infra_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Override the project-wide fake-Redis safety net for this tier only —
    reliability tests need the real thing."""
    monkeypatch.setenv("IDEMRA_DATABASE_URL", RELIABILITY_DATABASE_URL)
    monkeypatch.setenv("IDEMRA_REDIS_URL", RELIABILITY_REDIS_URL)

    if not _postgres_reachable():
        pytest.skip("real Postgres not reachable at localhost:5433 — run `docker compose up -d`")
    if not _redis_reachable():
        pytest.skip("real Redis not reachable at localhost:6379 — run `docker compose up -d`")


@pytest.fixture
def db() -> Iterator[Session]:
    engine = sqlalchemy.create_engine(RELIABILITY_DATABASE_URL)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


@pytest.fixture
def real_queue():
    from rq import Queue

    queue = Queue("apply", connection=redis_lib.Redis.from_url(RELIABILITY_REDIS_URL))
    queue.empty()
    try:
        yield queue
    finally:
        queue.empty()
