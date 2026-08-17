"""Master Agent routing — deterministic, rule-based, no LLM call.

route_task is pure (no DB access); record_routing_decision is the
separate persistence step, mirroring propose_changes/record_proposal's
existing pure-function + record-function split from Phase 3.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from idemra.db.models import Base, Run
from idemra.orchestrator.master import (
    CAPABILITY_CODE_CHANGE,
    RoutingDecision,
    record_routing_decision,
    route_task,
)
from idemra.orchestrator.runs import get_events


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


def test_route_task_rejects_empty_task(repo_root: Path) -> None:
    decision = route_task("", repo_root)

    assert decision.accepted is False
    assert decision.capability is None
    assert "empty" in decision.reason


def test_route_task_rejects_whitespace_only_task(repo_root: Path) -> None:
    decision = route_task("   \n\t  ", repo_root)

    assert decision.accepted is False


def test_route_task_rejects_nonexistent_repo(tmp_path: Path) -> None:
    missing_repo = tmp_path / "does-not-exist"

    decision = route_task("add a feature", missing_repo)

    assert decision.accepted is False
    assert decision.capability is None
    assert str(missing_repo) in decision.reason


def test_route_task_rejects_repo_path_that_is_a_file(tmp_path: Path) -> None:
    a_file = tmp_path / "not-a-dir.txt"
    a_file.write_text("x")

    decision = route_task("add a feature", a_file)

    assert decision.accepted is False


def test_route_task_accepts_valid_task_and_repo(repo_root: Path) -> None:
    decision = route_task("add a greeting function", repo_root)

    assert decision.accepted is True
    assert decision.capability == CAPABILITY_CODE_CHANGE
    assert decision.reason


def test_record_routing_decision_writes_event_for_acceptance(session: Session, repo_root: Path) -> None:
    run = Run(repo=str(repo_root), task="add a feature")
    session.add(run)
    session.flush()
    decision = RoutingDecision(accepted=True, capability=CAPABILITY_CODE_CHANGE, reason="only capability")

    record_routing_decision(session, run, decision)

    events = get_events(session, run.id)
    assert len(events) == 1
    assert events[0].type == "routing_decision"
    assert events[0].payload == {"accepted": True, "capability": CAPABILITY_CODE_CHANGE, "reason": "only capability"}


def test_record_routing_decision_writes_event_for_rejection(session: Session, repo_root: Path) -> None:
    run = Run(repo=str(repo_root), task="")
    session.add(run)
    session.flush()
    decision = RoutingDecision(accepted=False, capability=None, reason="task cannot be empty")

    record_routing_decision(session, run, decision)

    events = get_events(session, run.id)
    assert events[0].payload == {"accepted": False, "capability": None, "reason": "task cannot be empty"}
