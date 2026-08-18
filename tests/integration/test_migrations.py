"""Alembic against real Postgres — the one thing tests/unit/ structurally
cannot catch. Every DB test elsewhere in the suite runs SQLAlchemy's
Base.metadata.create_all() against SQLite (see tests/unit/test_cli_run.py
and friends), which never executes a single line of
migrations/versions/*.py. A migration that's out of sync with
db/models.py — a missing column, a wrong type, a constraint that doesn't
match — would pass every unit test and only surface the first time
someone runs `alembic upgrade head` against a real database.

Creates and drops a scratch Postgres database per test session rather than
touching the dev "idemra" database migrations/env.py defaults to. Skips
cleanly if Postgres isn't reachable (same treatment as tests/reliability/).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from idemra.db.models import Base
from idemra.db.session import DEFAULT_DATABASE_URL
from tests._live_infra import postgres_reachable

ADMIN_DATABASE_URL = DEFAULT_DATABASE_URL
TEST_DB_NAME = "idemra_migration_test"
TEST_DATABASE_URL = f"postgresql+psycopg://idemra:idemra@localhost:5433/{TEST_DB_NAME}"
ALEMBIC_INI = Path(__file__).parents[2] / "alembic.ini"


def _drop_test_db() -> None:
    engine = sqlalchemy.create_engine(ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
    finally:
        engine.dispose()


def _table_names(url: str) -> set[str]:
    engine = sqlalchemy.create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


@pytest.fixture
def migration_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    if not postgres_reachable(ADMIN_DATABASE_URL):
        pytest.skip("real Postgres not reachable at localhost:5433 — run `docker compose up -d`")

    # migrations/env.py reads IDEMRA_DATABASE_URL itself and overrides
    # whatever Config.set_main_option("sqlalchemy.url", ...) says — it has
    # to be pinned at the scratch DB here, not left to whatever a caller's
    # shell happens to have exported (the dev DB, most likely).
    monkeypatch.setenv("IDEMRA_DATABASE_URL", TEST_DATABASE_URL)

    _drop_test_db()
    engine = sqlalchemy.create_engine(ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    finally:
        engine.dispose()

    try:
        yield TEST_DATABASE_URL
    finally:
        _drop_test_db()


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_alembic_upgrade_head_creates_all_expected_tables(migration_db: str) -> None:
    command.upgrade(_alembic_config(migration_db), "head")

    tables = _table_names(migration_db)
    expected = {t.name for t in Base.metadata.tables.values()}
    assert expected <= tables, f"migration didn't create: {expected - tables}"


def test_alembic_schema_matches_orm_models_exactly(migration_db: str) -> None:
    """The real regression this test exists for: someone edits db/models.py
    (adds a column, changes a type) and forgets to generate/hand-write the
    matching migration. Every unit test still passes (SQLite's
    create_all() reads the ORM models directly, migrations never enter the
    picture) — only a live schema diff catches it."""
    command.upgrade(_alembic_config(migration_db), "head")

    engine = sqlalchemy.create_engine(migration_db)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert diff == [], f"migrations/versions/ has drifted from db/models.py: {diff}"


def test_alembic_downgrade_base_then_upgrade_head_is_reversible(migration_db: str) -> None:
    cfg = _alembic_config(migration_db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    tables_after_downgrade = _table_names(migration_db) - {"alembic_version"}
    assert tables_after_downgrade == set(), f"downgrade left tables behind: {tables_after_downgrade}"

    command.upgrade(cfg, "head")
    tables_after_reupgrade = _table_names(migration_db)
    expected = {t.name for t in Base.metadata.tables.values()}
    assert expected <= tables_after_reupgrade
