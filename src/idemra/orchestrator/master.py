"""Master Agent — routes a task to a capability, or rejects it before any
LLM call.

Deterministic, rule-based routing, not LLM-based: with a single real
capability today (code changes via the Coder Agent), there's no
natural-language nuance to resolve — the 2026 routing-pattern consensus is
that rule-based routing is faster, cheaper, and more deterministic for
fixed-intent classification like this.

AGENT_REGISTRY maps capability -> agent name for audit/logging only, not
capability -> callable. Making dispatch fully generic now would mean
guessing at a call signature for agent types that don't exist yet
(Self-Healing, etc. land in Phase 6+); a second real agent should inform
that interface, not speculation today.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from idemra.db.models import Run
from idemra.orchestrator.runs import record_event

CAPABILITY_CODE_CHANGE = "code_change"

AGENT_REGISTRY = {
    CAPABILITY_CODE_CHANGE: "coder_agent",
}


@dataclass(frozen=True)
class RoutingDecision:
    accepted: bool
    capability: str | None
    reason: str


def route_task(task: str, repo_root: Path) -> RoutingDecision:
    if not task or not task.strip():
        return RoutingDecision(accepted=False, capability=None, reason="task cannot be empty")

    if not repo_root.is_dir():
        return RoutingDecision(accepted=False, capability=None, reason=f"{repo_root} does not exist")

    return RoutingDecision(
        accepted=True, capability=CAPABILITY_CODE_CHANGE, reason="only capability registered"
    )


def record_routing_decision(session: Session, run: Run, decision: RoutingDecision) -> None:
    record_event(
        session,
        run,
        "routing_decision",
        {"accepted": decision.accepted, "capability": decision.capability, "reason": decision.reason},
        "routing",
    )
