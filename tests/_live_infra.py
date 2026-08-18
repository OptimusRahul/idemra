"""Shared "is real infra reachable" probes for the test tiers that talk to
real Postgres/Redis instead of SQLite/mocks (tests/reliability/,
tests/integration/test_migrations.py) — used to skip cleanly rather than
fail when a contributor doesn't have `docker compose up -d` running.
"""

from __future__ import annotations

import redis as redis_lib
import sqlalchemy


def postgres_reachable(url: str) -> bool:
    try:
        engine = sqlalchemy.create_engine(url)
        with engine.connect():
            return True
    except Exception:  # noqa: BLE001 — any connection failure means "skip", not "crash the run"
        return False
    finally:
        engine.dispose()


def redis_reachable(url: str) -> bool:
    try:
        return bool(redis_lib.Redis.from_url(url).ping())
    except Exception:  # noqa: BLE001 — any connection failure means "skip", not "crash the run"
        return False
