"""Postgres schema: single source of truth, event-sourced.

Every state change is an append to `events`. Current state of any run is a
fold over its event stream — never mutated in place. This is what makes
replay and audit the same mechanism instead of two separate claims.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    status: Mapped[str] = mapped_column(String, default="pending")
    # pending -> running -> awaiting_approval -> approved/rejected -> applying -> completed/failed/stale
    task: Mapped[str] = mapped_column(String)
    repo: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    seq: Mapped[int] = mapped_column()
    type: Mapped[str] = mapped_column(String)
    schema_version: Mapped[int] = mapped_column(default=1)
    payload: Mapped[dict] = mapped_column(JSON)
    # run_id:step_id — enforced unique (see UniqueConstraint below) so a retried/duplicated publish is a no-op, not a double-execution.
    idempotency_key: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        Index("ix_events_run_id_seq", "run_id", "seq"),
        UniqueConstraint("idempotency_key", name="uq_events_idempotency_key"),
    )


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    step_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending/approved/rejected/expired
    requested_at: Mapped[datetime] = mapped_column(default=_now)
    expires_at: Mapped[datetime] = mapped_column()
    decided_at: Mapped[datetime | None] = mapped_column(default=None)
    decided_by: Mapped[str | None] = mapped_column(String, default=None)
    reason: Mapped[str | None] = mapped_column(String, default=None)


class DeadLetter(Base):
    __tablename__ = "dead_letters"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    failure_reason: Mapped[str] = mapped_column(String)
    retry_count: Mapped[int] = mapped_column(default=0)
    moved_at: Mapped[datetime] = mapped_column(default=_now)
