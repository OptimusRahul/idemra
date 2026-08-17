"""Run lifecycle: propose -> validate -> approval gate -> apply.

The two tests that matter most: a Layer 1 or Layer 2 violation must never
result in a partially-written repo (fail-closed, all-or-nothing), and a
retried approve must never apply twice.
"""

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from idemra.agents.coder import ProposedFile
from idemra.config.permissions import PermissionsConfig
from idemra.db.models import Base
from idemra.orchestrator.runs import (
    apply_run,
    create_run,
    get_events,
    record_approval_decision,
    record_proposal,
    request_approval,
    start_run,
    validate_proposal,
)


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


DEFAULT_PERMISSIONS = PermissionsConfig(approval_required=("write",), denied_paths=())


def test_happy_path_writes_files_and_completes(session: Session, repo_root: Path) -> None:
    run = create_run(session, repo=str(repo_root), task="add a file")
    start_run(session, run)
    proposed = [ProposedFile(path="new.py", content="x = 1\n")]
    record_proposal(session, run, proposed)
    request_approval(session, run, step_id="apply", ttl=timedelta(hours=1))
    record_approval_decision(session, run.id, "approved")

    apply_run(session, run, repo_root, DEFAULT_PERMISSIONS)

    assert run.status == "completed"
    assert (repo_root / "new.py").read_text() == "x = 1\n"
    event_types = [e.type for e in get_events(session, run.id)]
    assert event_types == [
        "status_changed",  # running
        "change_proposed",
        "status_changed",  # awaiting_approval
        "status_changed",  # approved
        "status_changed",  # applying
        "files_applied",
        "status_changed",  # completed
    ]


def test_layer1_violation_fails_closed_zero_files_written(session: Session, repo_root: Path) -> None:
    run = create_run(session, repo=str(repo_root), task="escape the jail")
    start_run(session, run)
    proposed = [
        ProposedFile(path="ok.py", content="fine\n"),
        ProposedFile(path="../outside.py", content="escapes repo root\n"),
    ]
    record_proposal(session, run, proposed)

    apply_run(session, run, repo_root, DEFAULT_PERMISSIONS)

    assert run.status == "failed"
    assert not (repo_root / "ok.py").exists()  # all-or-nothing — the valid file is not written either
    assert not (repo_root.parent / "outside.py").exists()


def test_layer1_secret_denylist_fails_closed(session: Session, repo_root: Path) -> None:
    run = create_run(session, repo=str(repo_root), task="write a secret")
    start_run(session, run)
    record_proposal(session, run, [ProposedFile(path=".env", content="SECRET=1\n")])

    apply_run(session, run, repo_root, DEFAULT_PERMISSIONS)

    assert run.status == "failed"
    assert not (repo_root / ".env").exists()


def test_layer2_denied_path_fails_closed(session: Session, repo_root: Path) -> None:
    run = create_run(session, repo=str(repo_root), task="write a denied config")
    start_run(session, run)
    record_proposal(session, run, [ProposedFile(path="secrets/creds.json", content="{}\n")])
    permissions = PermissionsConfig(approval_required=("write",), denied_paths=("secrets/*.json",))

    apply_run(session, run, repo_root, permissions)

    assert run.status == "failed"
    assert not (repo_root / "secrets" / "creds.json").exists()


def test_validate_proposal_reports_every_violation_not_just_the_first(repo_root: Path) -> None:
    proposed = [
        ProposedFile(path=".env", content="x\n"),
        ProposedFile(path="../outside.py", content="x\n"),
    ]

    violations = validate_proposal(proposed, repo_root, DEFAULT_PERMISSIONS)

    assert len(violations) == 2


def test_reject_leaves_zero_files_written(session: Session, repo_root: Path) -> None:
    run = create_run(session, repo=str(repo_root), task="add a file")
    start_run(session, run)
    record_proposal(session, run, [ProposedFile(path="new.py", content="x = 1\n")])
    request_approval(session, run, step_id="apply", ttl=timedelta(hours=1))

    record_approval_decision(session, run.id, "rejected")

    assert run.status == "rejected"
    assert not (repo_root / "new.py").exists()


def test_retried_approve_never_applies_twice(session: Session, repo_root: Path) -> None:
    run = create_run(session, repo=str(repo_root), task="add a file")
    start_run(session, run)
    record_proposal(session, run, [ProposedFile(path="new.py", content="x = 1\n")])
    request_approval(session, run, step_id="apply", ttl=timedelta(hours=1))

    record_approval_decision(session, run.id, "approved")
    assert run.status == "approved"
    apply_run(session, run, repo_root, DEFAULT_PERMISSIONS)
    assert run.status == "completed"

    # Simulates a retried `idemra approve` call: the CLI only calls
    # apply_run when run.status == "approved" — by now it's "completed",
    # so a second apply_run call must never happen.
    record_approval_decision(session, run.id, "approved")  # no-op, Phase 2 behavior
    assert run.status == "completed"  # unchanged — the guard held

    event_types = [e.type for e in get_events(session, run.id)]
    assert event_types.count("files_applied") == 1
