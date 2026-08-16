"""Run introspection, approval decisions, and replay.

replay_run() folds only over the persisted event stream (never reads
runs.status), so these tests prove replay is a genuine reconstruction —
not an echo of cached state — and that a retried approve/reject is a
true idempotent no-op, matching the project's event-publishing model.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from idemra.db.models import Approval, Base, Run
from idemra.orchestrator.runs import (
    ApprovalConflict,
    InvalidDecision,
    NoPendingApproval,
    RunNotFound,
    get_events,
    get_run,
    list_runs,
    record_approval_decision,
    replay_run,
)


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _make_run(session: Session, task: str = "do something") -> Run:
    run = Run(task=task, repo="example/repo")
    session.add(run)
    session.flush()
    return run


def _make_pending_approval(session: Session, run: Run) -> Approval:
    approval = Approval(run_id=run.id, step_id="apply", expires_at=datetime.now(UTC) + timedelta(hours=1))
    session.add(approval)
    session.flush()
    return approval


def test_get_run_returns_the_run(session: Session) -> None:
    run = _make_run(session)

    assert get_run(session, run.id).id == run.id


def test_get_run_raises_for_unknown_id(session: Session) -> None:
    with pytest.raises(RunNotFound):
        get_run(session, "does-not-exist")


def test_list_runs_returns_all_runs_oldest_first(session: Session) -> None:
    first = _make_run(session, task="first")
    second = _make_run(session, task="second")

    runs = list_runs(session)

    assert [r.id for r in runs] == [first.id, second.id]


def test_get_events_raises_for_unknown_run(session: Session) -> None:
    with pytest.raises(RunNotFound):
        get_events(session, "does-not-exist")


def test_get_events_returns_empty_list_for_run_with_no_events(session: Session) -> None:
    run = _make_run(session)

    assert get_events(session, run.id) == []


def test_record_approval_decision_approves_and_writes_event(session: Session) -> None:
    run = _make_run(session)
    _make_pending_approval(session, run)

    approval = record_approval_decision(session, run.id, "approved")

    assert approval.status == "approved"
    events = get_events(session, run.id)
    assert len(events) == 1
    assert events[0].type == "status_changed"
    assert events[0].payload == {"status": "approved"}


def test_record_approval_decision_rejects_with_reason(session: Session) -> None:
    run = _make_run(session)
    _make_pending_approval(session, run)

    approval = record_approval_decision(session, run.id, "rejected", reason="not safe")

    assert approval.status == "rejected"
    assert approval.reason == "not safe"


def test_record_approval_decision_raises_when_none_requested(session: Session) -> None:
    run = _make_run(session)

    with pytest.raises(NoPendingApproval):
        record_approval_decision(session, run.id, "approved")


def test_record_approval_decision_rejects_unknown_decision(session: Session) -> None:
    run = _make_run(session)
    _make_pending_approval(session, run)

    with pytest.raises(InvalidDecision):
        record_approval_decision(session, run.id, "maybe")


def test_record_approval_decision_is_idempotent_on_retry(session: Session) -> None:
    run = _make_run(session)
    _make_pending_approval(session, run)

    first = record_approval_decision(session, run.id, "approved")
    second = record_approval_decision(session, run.id, "approved")

    assert first.id == second.id
    events = get_events(session, run.id)
    assert len(events) == 1  # retried publish is a no-op, not a second event


def test_record_approval_decision_conflicts_on_opposite_decision(session: Session) -> None:
    run = _make_run(session)
    _make_pending_approval(session, run)
    record_approval_decision(session, run.id, "approved")

    with pytest.raises(ApprovalConflict):
        record_approval_decision(session, run.id, "rejected")


def test_replay_run_folds_status_changed_events(session: Session) -> None:
    run = _make_run(session)
    _make_pending_approval(session, run)
    record_approval_decision(session, run.id, "approved")

    result = replay_run(session, run.id)

    assert result.status == "approved"
    assert result.applied_event_types == ["status_changed"]


def test_replay_run_defaults_to_pending_with_no_events(session: Session) -> None:
    run = _make_run(session)

    result = replay_run(session, run.id)

    assert result.status == "pending"
    assert result.applied_event_types == []
