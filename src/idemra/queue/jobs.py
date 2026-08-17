"""RQ job wiring — the "apply" queue that idemra approve and idemra run's
auto-apply branch both enqueue onto, and idemra worker consumes.

RQ's own synchronous test mode still requires a real Redis connection, so
this module is deliberately never exercised by the automated test suite
(same treatment Postgres gets — SQLite substitutes there, live Postgres
only in the manual smoke test). enqueue_apply is a monkeypatchable
module-level reference in cli/main.py so tests can simulate a worker
without touching real Redis; apply_run_job itself is tested as a plain
function, RQ-free.
"""

from __future__ import annotations

import os
from pathlib import Path

from redis import Redis
from rq import Queue

from idemra.config.permissions import (
    InvalidPermissionsConfig,
    PermissionsNotFound,
    load_permissions,
)
from idemra.db.session import session_scope
from idemra.orchestrator.runs import apply_run, fail_run, get_run

DEFAULT_REDIS_URL = "redis://localhost:6379/0"
QUEUE_NAME = "apply"


def get_redis_connection() -> Redis:
    url = os.environ.get("IDEMRA_REDIS_URL", DEFAULT_REDIS_URL)
    return Redis.from_url(url)


def get_queue() -> Queue:
    return Queue(QUEUE_NAME, connection=get_redis_connection())


def enqueue_apply(run_id: str) -> None:
    get_queue().enqueue(apply_run_job, run_id)


def apply_run_job(run_id: str) -> None:
    """Applies an approved run's stored proposal to disk.

    No-ops if the run isn't in "queued" state — the same idempotency
    guard shape as approve()'s "status != approved" check, so a retried
    or re-delivered RQ job never double-applies.
    """
    with session_scope() as session:
        run = get_run(session, run_id)
        if run.status != "queued":
            return

        try:
            permissions = load_permissions(Path(run.repo))
        except (PermissionsNotFound, InvalidPermissionsConfig) as exc:
            fail_run(session, run, str(exc))
            return

        apply_run(session, run, Path(run.repo), permissions)
