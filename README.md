# Idemra

Production reliability infrastructure for coding agents — idempotent event
log, crash recovery, and replay-as-audit for a repo-scoped agent runtime.

Not a framework, and not a multi-agent platform pitch. Individually, every
agent capability this project touches (self-healing, dependency watch,
memory, dashboards) is already commoditized by funded competitors — see
[ADR 0002](docs/adr/0002-postgres-event-sourcing.md) and the market-research
history in the project's Obsidian notes for the receipts. What isn't
commoditized, per a 2026 Sky9 Capital analysis, is the reliability layer
underneath: idempotency, checkpointing, crash recovery. That's what this
project actually builds and demonstrates.

Deliberately hand-rolled — no Temporal, Restate, or LangGraph underneath.
See [ADR 0004](docs/adr/0004-hand-rolled-not-temporal.md) for why that's a
feature of the plan, not a gap.

## Status

**Phase 1 — Skeleton & Permissions**, complete. **Phase 2 — World Model**,
in progress (target: 2026-09-12): repo structural snapshot, symbol index,
Layer 2 permissions parsing, and the remaining run-introspection CLI
commands (`status`/`approve`/`reject`/`log`/`replay`). Not usable yet.

Full phase plan, current milestone, and open issues: see
[Tracking](#tracking) below.

## Architecture

See [`docs/adr/`](docs/adr/) for every real decision and why it was made,
in order:

1. [Record architecture decisions](docs/adr/0001-record-architecture-decisions.md)
2. [Postgres as single source of truth, event-sourced](docs/adr/0002-postgres-event-sourcing.md)
3. [Orchestrated core, choreographed fan-out](docs/adr/0003-orchestrated-core-choreographed-fan-out.md)
4. [Hand-rolled orchestrator, not Temporal/Restate](docs/adr/0004-hand-rolled-not-temporal.md)

Short version: one Postgres instance is the source of truth, event-sourced.
The control plane (task intake, routing, approval gates) is orchestrated;
the execution plane (agents once dispatched) is choreographed over the same
log — later phases (Watch Mode, Self-Healing, Memory Graph) subscribe to
events on that log rather than needing a new invocation path.

## Tracking

Phase-level status lives in three places, each doing a different job — this
isn't redundant, each layer answers a question the others can't:

| Layer | Answers | Where |
|---|---|---|
| **GitHub Milestones** | Which phase are we in, and by when | [Milestones](../../milestones) — one per phase, due dates from the compressed 6-month schedule |
| **GitHub Issues** | What's the concrete remaining work in this phase | [Issues](../../issues), labeled `phase-N`, filed against the current milestone |
| **ADRs** (`docs/adr/`) | Why the system is built the way it is | Committed alongside the code they justify, never edited after acceptance — superseded by a new ADR instead |

Narrative/decision-history (market research, naming process, the full
6-month schedule reasoning) lives outside this repo, in Obsidian
(`Idemra Overview.md`, `Idemra Phase Plan.md`) — that's context for *why*
the plan looks like this, not something a contributor needs to operate the
code.

There's also a fourth, unfiltered layer: every commit on `main` auto-posts
a one-line summary to an Obsidian activity log and a Discord checkpoint via
`.githooks/post-commit`. This is machine-local automation (hardcoded paths
to this developer's vault and `discord-relay` install), not something a
fresh clone gets for free — see "Local development" below to enable it.

## Local development

```bash
docker compose -f docker/docker-compose.yml up -d
uv sync --all-groups
uv run pytest

# optional: enable the auto-post-to-Obsidian/Discord git hook
git config core.hooksPath .githooks
```

## License

Unreleased — no license granted yet.
