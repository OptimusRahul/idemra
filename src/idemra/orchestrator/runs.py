"""Run introspection plus the approval/replay mechanics event-sourcing
depends on.

No domain event types exist yet — no agent produces real run events
until Phase 3 — so the only event type this module writes and folds
over is "status_changed". That's enough to prove idempotent event
publishing and deterministic replay actually work, without inventing a
domain-specific reducer for events nothing emits yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from idemra.db.models import Approval, Event, Run

VALID_DECISIONS = frozenset({"approved", "rejected"})


class RunNotFound(Exception):
    pass


class NoPendingApproval(Exception):
    pass


class ApprovalConflict(Exception):
    pass


class InvalidDecision(Exception):
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


def record_approval_decision(
    session: Session, run_id: str, decision: str, reason: str | None = None
) -> Approval:
    if decision not in VALID_DECISIONS:
        raise InvalidDecision(f"decision must be one of {sorted(VALID_DECISIONS)}, got {decision!r}")

    get_run(session, run_id)
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

    event = Event(
        run_id=run_id,
        seq=_next_seq(session, run_id),
        type="status_changed",
        payload={"status": decision},
        # run_id:approval:<approval id>:decision — a retried/duplicated
        # approve/reject call is a no-op, not a double-decision.
        idempotency_key=f"{run_id}:approval:{approval.id}:decision",
    )
    session.add(event)
    session.flush()
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
