# 3. Orchestrated core, choreographed fan-out

## Status
Accepted

## Context
Pure event-driven choreography (pub/sub for everything) is already
productized — Restate and Motia ship durable, replayable, human-in-the-loop
execution natively. Pure DAG orchestration can't support a process that
reacts to events without being explicitly invoked, which later phases
(Watch Mode, Self-Healing, Memory Graph extraction) require.

## Decision
Hybrid. The control plane — task intake, Master Agent routing decisions,
approval gates — is orchestrated: centralized, auditable, state-machine
driven. The execution plane — specialist agents once routed — is
choreographed: they publish/subscribe to events on the same Postgres-backed
log without the orchestrator micromanaging every step.

## Consequences
- Compliance-relevant decisions (routing, approval) stay centrally
  auditable.
- Phase 12's Watch Mode needs no new invocation path — it's a subscriber to
  event types it cares about, using infrastructure that exists from Phase 1.
