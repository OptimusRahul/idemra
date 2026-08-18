"""Crash-recovery and idempotency tests against real Postgres + real Redis.

Everything in tests/unit/ verifies these same guards (record_event's
idempotency_key, apply_run_job's status != "queued" check) as plain
function calls against SQLite. This file verifies the guards actually hold
under the real failure modes the project claims to survive: an RQ job
redelivered after a worker dies, and a worker process dying mid-apply
before its DB transaction commits. See tests/reliability/conftest.py for
why real infra is required here and how it's skipped when unavailable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rq import Worker
from sqlalchemy.orm import Session

from idemra.agents.coder import ProposedFile
from idemra.config.scaffold import write_scaffold
from idemra.orchestrator.runs import (
    apply_run,
    create_run,
    get_events,
    get_run,
    mark_queued_for_apply,
    record_proposal,
)
from idemra.queue.jobs import apply_run_job


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write_scaffold(tmp_path)
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    return tmp_path


def _seeded_run(db: Session, repo: Path):
    run = create_run(db, str(repo), "add a subtract function")
    record_proposal(
        db,
        run,
        [ProposedFile(path="calc.py", content="def subtract(a, b):\n    return a - b\n")],
    )
    mark_queued_for_apply(db, run)
    db.commit()
    return run


def test_redelivered_apply_job_only_applies_once(db: Session, repo: Path, real_queue) -> None:
    """RQ's at-least-once delivery means the same job can be enqueued twice
    after a worker dies before ack'ing. apply_run_job's status guard must
    make the second delivery a no-op, not a double-apply."""
    run = _seeded_run(db, repo)

    real_queue.enqueue(apply_run_job, run.id)
    real_queue.enqueue(apply_run_job, run.id)  # simulated redelivery

    worker = Worker([real_queue], connection=real_queue.connection)
    worker.work(burst=True)

    db.expire_all()
    finished = get_run(db, run.id)
    assert finished.status == "completed"
    assert (repo / "calc.py").read_text() == "def subtract(a, b):\n    return a - b\n"

    events = get_events(db, run.id)
    assert [e.type for e in events].count("files_applied") == 1
    completed_events = [e for e in events if e.type == "status_changed" and e.payload["status"] == "completed"]
    assert len(completed_events) == 1


def test_worker_crash_before_commit_leaves_run_queued_and_retry_is_safe(
    db: Session, repo: Path
) -> None:
    """apply_run() writes files to disk (a real, non-transactional side
    effect) and records events in the same DB session that session_scope
    commits only once, at the very end. A process that dies between the
    file write and that commit leaves the DB transaction rolled back —
    run.status reverts to "queued" — so the next attempt is a clean,
    idempotent retry rather than a resume of a half-applied change."""
    run = _seeded_run(db, repo)

    from idemra.config.permissions import load_permissions

    permissions = load_permissions(repo)
    apply_run(db, run, repo, permissions)  # simulates the crash: never committed below
    assert (repo / "calc.py").read_text() == "def subtract(a, b):\n    return a - b\n"

    db.rollback()  # the crash: process dies before session_scope's commit

    db.expire_all()
    crashed_run = get_run(db, run.id)
    assert crashed_run.status == "queued"  # reverted to the last real commit, not "completed"
    # only the pre-crash commit's events survive — nothing from apply_run's
    # own uncommitted transaction (no "applying" or "files_applied")
    events_after_crash = get_events(db, run.id)
    assert [e.type for e in events_after_crash] == ["change_proposed", "status_changed"]

    apply_run_job(run.id)  # the retry, in a fresh session_scope (real commit this time)

    db.expire_all()
    retried_run = get_run(db, run.id)
    assert retried_run.status == "completed"
    assert (repo / "calc.py").read_text() == "def subtract(a, b):\n    return a - b\n"

    events = get_events(db, run.id)
    assert [e.type for e in events].count("files_applied") == 1


def test_apply_run_job_noops_when_run_already_completed(db: Session, repo: Path) -> None:
    """A job that finishes normally, then gets redelivered anyway (e.g. an
    ack that never reached Redis before the worker restarted) must not
    reprocess a run that's already terminal."""
    run = _seeded_run(db, repo)
    apply_run_job(run.id)
    db.expire_all()
    assert get_run(db, run.id).status == "completed"
    events_after_first_apply = len(get_events(db, run.id))

    apply_run_job(run.id)  # redelivered after completion

    db.expire_all()
    assert get_run(db, run.id).status == "completed"
    assert len(get_events(db, run.id)) == events_after_first_apply
