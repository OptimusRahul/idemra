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

from idemra.agents.coder import ProposedFile
from idemra.cli.main import app
from idemra.db.models import Approval, Base, Run
from idemra.orchestrator.runs import record_proposal
from idemra.queue.jobs import apply_run_job

runner = CliRunner()


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("IDEMRA_DATABASE_URL", f"sqlite:///{db_path}")
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_run_with_pending_approval(db, repo: str = "example/repo") -> str:
    session = db()
    run = Run(task="fix the bug", repo=repo)
    session.add(run)
    session.flush()
    session.add(Approval(run_id=run.id, step_id="apply", expires_at=datetime.now(UTC) + timedelta(hours=1)))
    session.commit()
    run_id = run.id
    session.close()
    return run_id


def _seed_run_with_proposal_and_pending_approval(db, repo_root: Path) -> str:
    """Like _seed_run_with_pending_approval, but with a real repo path +
    permissions.yml + a stored proposal — the shape `idemra approve` now
    needs since it applies the change, not just records the decision."""
    (repo_root / ".idemra").mkdir()
    (repo_root / ".idemra" / "permissions.yml").write_text("approval_required:\n  - write\ndenied_paths: []\n")

    session = db()
    run = Run(task="fix the bug", repo=str(repo_root))
    session.add(run)
    session.flush()
    record_proposal(session, run, [ProposedFile(path="fixed.py", content="fixed = True\n")])
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


def test_approve_then_log_then_replay(db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("idemra.cli.main.enqueue_apply", lambda run_id: None)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_id = _seed_run_with_proposal_and_pending_approval(db, repo_root)

    approve_result = runner.invoke(app, ["approve", run_id])
    assert approve_result.exit_code == 0
    assert "Approved" in approve_result.stdout

    apply_run_job(run_id)  # simulates `idemra worker` picking the job up

    assert (repo_root / "fixed.py").read_text() == "fixed = True\n"

    log_result = runner.invoke(app, ["log", run_id])
    assert log_result.exit_code == 0
    assert "status_changed" in log_result.stdout
    assert "files_applied" in log_result.stdout

    replay_result = runner.invoke(app, ["replay", run_id])
    assert replay_result.exit_code == 0
    assert "completed" in replay_result.stdout


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


def test_sweep_with_nothing_to_expire(db) -> None:
    result = runner.invoke(app, ["sweep"])

    assert result.exit_code == 0
    assert "No stale approvals" in result.stdout


def test_sweep_expires_past_ttl_approval_and_reports_it(db) -> None:
    session = db()
    run = Run(task="stuck run", repo="example/repo")
    session.add(run)
    session.flush()
    session.add(
        Approval(run_id=run.id, step_id="apply", expires_at=datetime.now(UTC) - timedelta(hours=1))
    )
    session.commit()
    run_id = run.id
    session.close()

    result = runner.invoke(app, ["sweep"])

    assert result.exit_code == 0
    assert "Expired 1 approval" in result.stdout
    assert run_id in result.stdout

    status_result = runner.invoke(app, ["status", run_id])
    assert "stale" in status_result.stdout
