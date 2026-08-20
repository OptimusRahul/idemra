#!/usr/bin/env python3
"""Seed a Run (optionally with a proposal and/or a pending approval)
directly against the real DB — used by scripts/verify-phases.sh to
exercise the Phase 5 queue/worker/approve/sweep pipeline without needing
a real LLM call. Prints the run id on success.

The one place this seeding logic lives, called with different args
instead of duplicated inline in three separate `uv run python -c` bash
heredocs — the same "one construction site" precedent record_event()
follows in orchestrator/runs.py.
"""

from __future__ import annotations

import argparse
from datetime import timedelta

from idemra.agents.coder import ProposedFile
from idemra.db.session import session_scope
from idemra.orchestrator.runs import create_run, record_proposal, request_approval


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--proposal-path", help="File the seeded proposal writes to, if any.")
    parser.add_argument("--proposal-content", default="", help="Content the seeded proposal writes.")
    parser.add_argument(
        "--ttl-seconds",
        type=int,
        help="Approval TTL in seconds (negative = already expired). Omit to skip requesting an approval.",
    )
    args = parser.parse_args()

    with session_scope() as session:
        run = create_run(session, args.repo, args.task)
        if args.proposal_path:
            proposed = [ProposedFile(path=args.proposal_path, content=args.proposal_content)]
            record_proposal(session, run, proposed)
        if args.ttl_seconds is not None:
            request_approval(session, run, "write", timedelta(seconds=args.ttl_seconds))
        session.flush()
        print(run.id)


if __name__ == "__main__":
    main()
