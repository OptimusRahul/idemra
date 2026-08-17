"""apply_run_job — tested as a plain function, RQ-free.

RQ's own synchronous test mode still needs a real Redis connection, so
this suite never touches real RQ machinery (same treatment Postgres
gets — SQLite substitutes for the automated suite, live infra only in
the manual smoke test). enqueue_apply/get_queue are covered there.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from idemra.agents.coder import ProposedFile
from idemra.db.models import Base
from idemra.orchestrator.runs import (
    create_run,
    get_run,
    mark_queued_for_apply,
    record_proposal,
    start_run,
)
from idemra.queue.jobs import apply_run_job


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("IDEMRA_DATABASE_URL", f"sqlite:///{db_path}")
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".idemra").mkdir()
    (root / ".idemra" / "permissions.yml").write_text("approval_required:\n  - write\ndenied_paths: []\n")
    return root


def _make_queued_run(db, repo_root: Path) -> str:
    session = db()
    run = create_run(session, repo=str(repo_root), task="add a file")
    start_run(session, run)
    record_proposal(session, run, [ProposedFile(path="new.py", content="x = 1\n")])
    mark_queued_for_apply(session, run)
    session.commit()
    run_id = run.id
    session.close()
    return run_id


def test_apply_run_job_applies_when_queued(db, repo_root: Path) -> None:
    run_id = _make_queued_run(db, repo_root)

    apply_run_job(run_id)

    assert (repo_root / "new.py").read_text() == "x = 1\n"


def test_apply_run_job_noops_when_not_queued(db, repo_root: Path) -> None:
    session = db()
    run = create_run(session, repo=str(repo_root), task="add a file")
    start_run(session, run)
    record_proposal(session, run, [ProposedFile(path="new.py", content="x = 1\n")])
    # never queued — status is "running", not "queued"
    session.commit()
    run_id = run.id
    session.close()

    apply_run_job(run_id)

    assert not (repo_root / "new.py").exists()


def test_apply_run_job_fails_run_when_permissions_missing(db, repo_root: Path) -> None:
    (repo_root / ".idemra" / "permissions.yml").unlink()
    run_id = _make_queued_run(db, repo_root)

    apply_run_job(run_id)  # must not raise

    session = db()
    refreshed = get_run(session, run_id)
    assert refreshed.status == "failed"
    session.close()
