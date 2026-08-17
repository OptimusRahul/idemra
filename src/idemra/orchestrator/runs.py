"""Run lifecycle: creation, world-model-driven proposal, the approval gate,
and applying a change to disk — plus the introspection/replay mechanics
event-sourcing depends on.

Two event types beyond "status_changed" exist as of Phase 3:
"change_proposed" (the Coder Agent's full proposal, stored verbatim so
apply_run and replay never re-invoke the LLM — ADR2) and "files_applied"
(which paths were actually written). replay_run needs no changes for
either — it already folds generically over any event type, only treating
"status_changed" specially for status reconstruction.

run.status is a materialized cache of the same fold replay_run performs
independently from the event log; every transition function here updates
both in the same flush so the two never diverge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from idemra.agents.coder import ProposedFile
from idemra.config.permissions import PermissionsConfig
from idemra.db.models import Approval, Event, Run
from idemra.permissions.layer1 import (
    PermissionViolation,
    assert_not_denied_path,
    assert_within_repo_root,
)
from idemra.permissions.layer2 import assert_not_layer2_denied

VALID_DECISIONS = frozenset({"approved", "rejected"})


class RunNotFound(Exception):
    pass


class NoPendingApproval(Exception):
    pass


class ApprovalConflict(Exception):
    pass


class InvalidDecision(Exception):
    pass


class NoProposalFound(Exception):
    pass


@dataclass(frozen=True)
class ReplayResult:
    run_id: str
    status: str
    applied_event_types: list[str] = field(default_factory=list)


def get_run(session: Session, run_id: str) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise RunNotFound(f"no run with id {run_id}")
    return run


def list_runs(session: Session) -> list[Run]:
    return list(session.scalars(select(Run).order_by(Run.created_at)))


def get_events(session: Session, run_id: str) -> list[Event]:
    get_run(session, run_id)  # 404 loudly instead of returning a misleading empty list
    return list(session.scalars(select(Event).where(Event.run_id == run_id).order_by(Event.seq)))


def _next_seq(session: Session, run_id: str) -> int:
    max_seq = session.scalars(
        select(Event.seq).where(Event.run_id == run_id).order_by(Event.seq.desc())
    ).first()
    return (max_seq or 0) + 1


def record_event(
    session: Session, run: Run, event_type: str, payload: dict, idempotency_suffix: str
) -> Event:
    """The single Event(...) construction site — every write below goes
    through this, so sequencing and the idempotency-key convention
    (f"{run.id}:{suffix}") only need to be gotten right once."""
    event = Event(
        run_id=run.id,
        seq=_next_seq(session, run.id),
        type=event_type,
        payload=payload,
        idempotency_key=f"{run.id}:{idempotency_suffix}",
    )
    session.add(event)
    session.flush()
    return event


def record_approval_decision(
    session: Session, run_id: str, decision: str, reason: str | None = None
) -> Approval:
    if decision not in VALID_DECISIONS:
        raise InvalidDecision(f"decision must be one of {sorted(VALID_DECISIONS)}, got {decision!r}")

    run = get_run(session, run_id)
    approval = session.scalars(
        select(Approval).where(Approval.run_id == run_id).order_by(Approval.requested_at.desc())
    ).first()
    if approval is None:
        raise NoPendingApproval(f"no approval requested for run {run_id}")

    if approval.status == decision:
        return approval  # retried publish of the same decision — no-op, not a second event

    if approval.status != "pending":
        raise ApprovalConflict(
            f"approval for run {run_id} was already {approval.status}, cannot record {decision}"
        )

    approval.status = decision
    approval.decided_at = datetime.now(UTC)
    approval.reason = reason
    run.status = decision  # keep the materialized run.status in sync with the log

    # approval:<approval id>:decision — a retried/duplicated approve/reject
    # call is a no-op, not a double-decision.
    record_event(session, run, "status_changed", {"status": decision}, f"approval:{approval.id}:decision")
    return approval


def replay_run(session: Session, run_id: str) -> ReplayResult:
    """Reconstruct run state deterministically by folding over the event stream.

    Never reads `runs.status` — this is what makes replay a genuine
    reconstruction from the log, not just an echo of cached state.
    """
    events = get_events(session, run_id)

    status = "pending"
    applied: list[str] = []
    for event in events:
        applied.append(event.type)
        if event.type == "status_changed":
            status = event.payload["status"]

    return ReplayResult(run_id=run_id, status=status, applied_event_types=applied)


def create_run(session: Session, repo: str, task: str) -> Run:
    run = Run(repo=repo, task=task)
    session.add(run)
    session.flush()
    return run


def _write_status_event(
    session: Session, run: Run, status: str, idempotency_suffix: str, extra_payload: dict | None = None
) -> None:
    run.status = status
    payload = {"status": status, **(extra_payload or {})}
    record_event(session, run, "status_changed", payload, idempotency_suffix)


def start_run(session: Session, run: Run) -> None:
    _write_status_event(session, run, "running", "start")


def record_proposal(session: Session, run: Run, proposed: list[ProposedFile]) -> None:
    """Store the Coder Agent's full proposal verbatim.

    apply_run and replay_run both read this back instead of ever calling
    the LLM again — ADR2's "replay reconstructs from what happened, never
    re-invokes the LLM" promise, made real rather than just asserted.
    """
    record_event(
        session,
        run,
        "change_proposed",
        {"files": [{"path": f.path, "content": f.content} for f in proposed]},
        "proposal",
    )


def _get_proposal(session: Session, run_id: str) -> list[ProposedFile]:
    event = session.scalars(
        select(Event)
        .where(Event.run_id == run_id, Event.type == "change_proposed")
        .order_by(Event.seq.desc())
    ).first()
    if event is None:
        raise NoProposalFound(f"no change_proposed event for run {run_id}")
    return [ProposedFile(path=f["path"], content=f["content"]) for f in event.payload["files"]]


def request_approval(session: Session, run: Run, step_id: str, ttl: timedelta) -> Approval:
    approval = Approval(run_id=run.id, step_id=step_id, expires_at=datetime.now(UTC) + ttl)
    session.add(approval)
    _write_status_event(session, run, "awaiting_approval", "await-approval")
    return approval


def fail_run(session: Session, run: Run, reason: str) -> None:
    _write_status_event(session, run, "failed", "fail", extra_payload={"reason": reason})


def validate_proposal(
    proposed: list[ProposedFile], repo_root: Path, permissions: PermissionsConfig
) -> list[str]:
    """Check every proposed path against Layer 1 + Layer 2. Returns a list
    of violation messages (empty = all clear) rather than raising on the
    first failure, so a caller can report every problem at once."""
    violations = []
    for proposed_file in proposed:
        target = repo_root / proposed_file.path
        try:
            assert_within_repo_root(target, repo_root)
            assert_not_denied_path(target)
            assert_not_layer2_denied(target, permissions)
        except PermissionViolation as exc:
            violations.append(str(exc))
    return violations


def apply_run(session: Session, run: Run, repo_root: Path, permissions: PermissionsConfig) -> None:
    """Re-reads the stored proposal (never re-invokes the LLM) and writes
    it to disk. All-or-nothing: every path is validated before anything is
    written, so a run never leaves the repo half-changed."""
    proposed = _get_proposal(session, run.id)

    violations = validate_proposal(proposed, repo_root, permissions)
    if violations:
        fail_run(session, run, "; ".join(violations))
        return

    _write_status_event(session, run, "applying", "apply-start")

    for proposed_file in proposed:
        target = repo_root / proposed_file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(proposed_file.content)

    record_event(session, run, "files_applied", {"paths": [f.path for f in proposed]}, "files-applied")
    _write_status_event(session, run, "completed", "complete")
