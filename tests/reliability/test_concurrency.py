"""Concurrent-writer races against real Postgres.

record_approval_decision's "already decided" guard (orchestrator/runs.py)
reads approval.status from the Python object in its own session before
deciding whether to raise ApprovalConflict. Under real concurrent access,
two sessions can both read status="pending" before either commits — the
in-memory guard alone can't prevent that, only a DB-level constraint
checked at commit time can. This file exercises that race for real rather
than assuming the guard is sufficient just because it reads correctly in
a single-threaded unit test.
"""

from __future__ import annotations

import os
import threading
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy
from sqlalchemy.orm import Session, sessionmaker

from idemra.agents.coder import ProposedFile
from idemra.config.scaffold import write_scaffold
from idemra.orchestrator.runs import (
    ApprovalConflict,
    create_run,
    get_events,
    get_run,
    record_approval_decision,
    record_proposal,
    request_approval,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write_scaffold(tmp_path)
    return tmp_path


def _fresh_session() -> Session:
    """A brand-new engine+session, standing in for a separate process —
    the `db` fixture's single shared Session isn't safe to touch from two
    threads at once, and would hide exactly the race being tested."""
    engine = sqlalchemy.create_engine(os.environ["IDEMRA_DATABASE_URL"])
    return sessionmaker(bind=engine)()


def test_concurrent_conflicting_approve_and_reject_leave_a_consistent_final_state(
    db: Session, repo: Path
) -> None:
    run = create_run(db, str(repo), "concurrency race target")
    record_proposal(db, run, [ProposedFile(path="x.py", content="x = 1\n")])
    request_approval(db, run, "write", timedelta(hours=24))
    db.commit()
    run_id = run.id

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def decide(name: str, decision: str) -> None:
        session = _fresh_session()
        try:
            # Force both threads to have already read the "pending" row
            # before either attempts to commit a decision — the actual
            # race window record_approval_decision cannot see from inside
            # a single session.
            get_run(session, run_id)
            barrier.wait(timeout=5)
            record_approval_decision(session, run_id, decision)
            session.commit()
            results[name] = "committed"
        except ApprovalConflict as exc:
            session.rollback()
            results[name] = ("ApprovalConflict", str(exc))
        finally:
            session.close()

    t1 = threading.Thread(target=decide, args=("approve", "approved"))
    t2 = threading.Thread(target=decide, args=("reject", "rejected"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Whatever happened, the two ground truths the project's event-sourcing
    # claim depends on must still hold: the event log and the materialized
    # run/approval status must agree, and only one decision was ever
    # durably recorded for this approval.
    db.expire_all()
    final_run = get_run(db, run_id)
    events = get_events(db, run_id)
    decision_events = [e for e in events if e.type == "status_changed" and e.payload["status"] in ("approved", "rejected")]

    assert len(decision_events) == 1, (
        f"expected exactly one durable decision, got {[e.payload for e in decision_events]}; "
        f"thread results: {results}"
    )
    assert final_run.status == decision_events[0].payload["status"], (
        "run.status has diverged from the event log after the race — "
        f"run.status={final_run.status!r}, log says {decision_events[0].payload['status']!r}; "
        f"thread results: {results}"
    )

    # The loser of the race must see the same clean ApprovalConflict a
    # sequential double-decide raises — not a raw IntegrityError leaking
    # out of record_approval_decision's internals.
    outcomes = list(results.values())
    assert outcomes.count("committed") == 1, f"expected exactly one clean commit, got: {results}"
    conflict_outcomes = [v for v in outcomes if isinstance(v, tuple) and v[0] == "ApprovalConflict"]
    assert len(conflict_outcomes) == 1, f"expected exactly one ApprovalConflict, got: {results}"
