# TODOS

## Phase 6 — GitHub & Self-Healing

### Always-on trigger infrastructure (polling or webhook)

**What:** Give idemra a background way to notice a new labeled GitHub issue
or failed CI check without a human running `--from-issue`/`--from-check` by
hand — either a polling loop (same shape as `idemra worker`/`idemra sweep`)
or a webhook endpoint.

**Why:** D5 (Phase 6 scoping, 2026-08-19) chose a manual CLI flag
specifically to prove the GitHub-as-task-source idea before building
always-on infrastructure. This is the natural next step once the manual
flow is used enough to justify it.

**Context:** Depends on `docs/designs/phase-6-github-self-healing.md`
shipping first and the manual `--from-issue`/`--from-check` flow proving
useful in practice. Polling needs a scheduler + "already processed" state
tracking; a webhook needs a public endpoint or tunnel plus new server
code — heavier than anything currently in idemra. Once triggering is
autonomous, the "Autonomous-apply / narrow auto-approve design" TODO below
becomes more urgent too (an unattended trigger feeding a human-gated apply
is a weaker product than a fully autonomous one, but skipping the gate
without a real design is the wrong tradeoff).

**Effort:** L
**Priority:** P3
**Depends on:** Phase 6 (GitHub Issue/CI-failure manual trigger) shipping

### Autonomous-apply / narrow auto-approve design

**What:** A real design for letting some subset of GitHub/CI-triggered runs
skip the human approval gate (e.g. only test-file diffs, or only a
specific label) — the option D4 explicitly declined for Phase 6.

**Why:** "Self-healing" eventually implies some autonomy. D4 (Phase 6
scoping, 2026-08-19) kept the existing approval gate deliberately rather
than deciding this as a side effect of a 2-file minimal slice.

**Context:** Depends on Phase 6 shipping and the approval gate proving to
be an actual bottleneck in practice, not just a theoretical limitation.
This needs its own scoping pass (its own `/office-hours` +
`/plan-eng-review`), not something picked up casually — it's a new
permission-layer design, not a drop-in change to the existing
`approval_required` config.

**Effort:** L
**Priority:** P3
**Depends on:** Phase 6 shipping; ideally also the always-on trigger TODO above
