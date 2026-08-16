"""CLI wiring for status/approve/reject/log/replay against a real (SQLite,
for test speed) schema — no agent produces real runs until Phase 3, so
runs/approvals are seeded directly, same as the issue's done-when bar allows.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from idemra.cli.main import app
from idemra.db.models import Approval, Base, Run

runner = CliRunner()


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("IDEMRA_DATABASE_URL", f"sqlite:///{db_path}")
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_run_with_pending_approval(db) -> str:
    session = db()
    run = Run(task="fix the bug", repo="example/repo")
    session.add(run)
    session.flush()
    session.add(Approval(run_id=run.id, step_id="apply", expires_at=datetime.now(UTC) + timedelta(hours=1)))
    session.commit()
    run_id = run.id
    session.close()
    return run_id


def test_status_with_no_runs(db) -> None:
    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "No runs yet" in result.stdout


def test_status_all_runs(db) -> None:
    run_id = _seed_run_with_pending_approval(db)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert run_id in result.stdout


def test_status_single_run(db) -> None:
    run_id = _seed_run_with_pending_approval(db)

    result = runner.invoke(app, ["status", run_id])

    assert result.exit_code == 0
    assert "fix the bug" in result.stdout


def test_status_unknown_run_fails(db) -> None:
    result = runner.invoke(app, ["status", "does-not-exist"])

    assert result.exit_code == 1


def test_approve_then_log_then_replay(db) -> None:
    run_id = _seed_run_with_pending_approval(db)

    approve_result = runner.invoke(app, ["approve", run_id])
    assert approve_result.exit_code == 0
    assert "Approved" in approve_result.stdout

    log_result = runner.invoke(app, ["log", run_id])
    assert log_result.exit_code == 0
    assert "status_changed" in log_result.stdout

    replay_result = runner.invoke(app, ["replay", run_id])
    assert replay_result.exit_code == 0
    assert "approved" in replay_result.stdout


def test_reject_with_reason(db) -> None:
    run_id = _seed_run_with_pending_approval(db)

    result = runner.invoke(app, ["reject", run_id, "--reason", "not safe"])

    assert result.exit_code == 0
    assert "Rejected" in result.stdout


def test_approve_unknown_run_fails(db) -> None:
    result = runner.invoke(app, ["approve", "does-not-exist"])

    assert result.exit_code == 1


def test_log_unknown_run_fails(db) -> None:
    result = runner.invoke(app, ["log", "does-not-exist"])

    assert result.exit_code == 1
