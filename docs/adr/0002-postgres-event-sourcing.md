# 2. Postgres as single source of truth, event-sourced

## Status
Accepted

## Context
Every state change needs to be both recoverable after a crash and audit
sourced from the same event log used for compliance-grade audit and system
recovery. Two separate mechanisms for "what happened" and "what state are we
in" inevitably drift.

## Decision
One Postgres instance. `events` is append-only; current state of any `run`
is a fold over its event stream, never mutated in place. No secondary store
(Chroma, a separate audit DB) unless a phase specifically justifies it.

## Consequences
- Replay reconstructs state from *recorded* event payloads, not by
  re-invoking the LLM — this is what makes "replay = audit" true for
  non-deterministic LLM calls, not just asserted.
- `schema_version` on every event row means old events are never silently
  misread when the schema evolves.
- Failed events move to `dead_letters` after N retries instead of retrying
  forever or being silently dropped.
