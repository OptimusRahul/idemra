# Idemra

Production reliability infrastructure for coding agents — idempotent event
log, crash recovery, and replay-as-audit for a repo-scoped agent runtime.

Not a framework. A small, hand-rolled orchestrator over a single Postgres
event log, deliberately built without Temporal/Restate/LangGraph
underneath — see [ADR 0004](docs/adr/0004-hand-rolled-not-temporal.md) for
why.

## Status

Phase 1 — Skeleton & Permissions. Not usable yet.

## Architecture

See [`docs/adr/`](docs/adr/) for the real decisions and why they were made.
Short version: one Postgres instance is the source of truth, event-sourced;
the control plane (task intake, routing, approval gates) is orchestrated,
the execution plane (agents once dispatched) is choreographed over the same
log.

## Local development

```bash
docker compose -f docker/docker-compose.yml up -d
uv sync --all-groups
uv run pytest
```

## License

Unreleased — no license granted yet.
