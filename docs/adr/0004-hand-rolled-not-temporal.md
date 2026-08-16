# 4. Hand-rolled orchestrator, not Temporal/Restate

## Status
Accepted

## Context
Temporal, Restate, and LangGraph Platform all ship durable execution,
checkpointing, and replay as mature, funded products. Building on one of
them would ship faster. But the entire reason this project exists is to
build hands-on DevOps/MLOps reps in idempotency, checkpointing, and crash
recovery — reps that don't happen if a platform provides them for free.

## Decision
Hand-roll the orchestrator FSM, idempotency keys, and crash recovery
directly against Postgres. No workflow engine underneath.

## Consequences
- Slower to ship than adopting Temporal.
- Every reliability property in this system is one this project's author
  actually built and can defend in detail — the interview asset the
  research repeatedly identified as the real differentiator, independent of
  whether the underlying pattern is novel.
