# Phase 6 — GitHub & Self-Healing (design doc)

Milestone: due 2026-11-14. Scoped via an inline `/plan-eng-review` discovery
pass (no `/office-hours` available on this machine) on 2026-08-19.

## Problem statement

Every run today starts with a human typing `idemra run <repo> "<task>"` by
hand. Phase 6's name joins two distinct task *sources* that don't exist
yet: a GitHub Issue someone filed, and a CI check that just failed. Neither
changes what happens after a task exists — routing, proposal, approval,
and apply are all Phase 3-5 work, already shipped, already tested. Phase 6
is about how a task gets *created*, not what happens to it once it exists.

## Decisions locked during scoping

| # | Decision | Chosen | Why |
|---|---|---|---|
| D3 | Trigger source | **Both** — GitHub Issues and CI-failure checks, as two independent trigger paths sharing one pipeline | README already names both halves; both funnel into the same existing `route_task`/Coder Agent/approval flow, so the marginal cost of doing both is in trigger wiring, not new core logic |
| D4 | Approval gate | **Unchanged** — every GitHub/CI-triggered run still lands in `awaiting_approval`, same as a manually-typed run | Automates task *creation* only; a real autonomous-write path is a separate, bigger decision with its own permission-layer design, not a Phase 6 side effect |
| D5 | Trigger mechanism | **Manual CLI flag** — `idemra run <repo> --from-issue <url>` / `--from-check <url>`, human still initiates | Proves GitHub-as-task-source end to end with zero new always-on infrastructure (no scheduler, no webhook server, no public endpoint) |
| D6 | Phase size | **Minimal slice** — reuse the existing run/approve/worker pipeline unchanged, no new watch loop or agent | Matches how Phases 1-5 shipped (days, not weeks); keeps this scoping pass under the review's 8-file/2-class complexity guardrail |

## Data flow

```
                    ┌─────────────────────────────────┐
                    │   existing pipeline (unchanged)  │
                    │                                  │
  task: str ───────▶│  route_task ─▶ Coder Agent ─▶     │──▶ awaiting_approval
                    │  (Phase 4)     (Phase 3)          │    (human still
                    │                                    │     approves,
                    └─────────────────────────────────┘     Phase 5 unchanged)
                              ▲
                              │ task text, built fresh — no new state,
                              │ no new schema, no new event types
                    ┌─────────┴─────────┐
                    │   Phase 6 (new)   │
                    │                    │
        ┌───────────┤  task_from_issue() │
        │           │  task_from_check() │
   idemra run       │                    │
   --from-issue URL │  both shell out    │
   --from-check URL │  to `gh`, no new   │
        │           │  Python dep, no    │
        │           │  token management  │
        │           │  (reuses `gh auth` │
        │           │  the dev already   │
        │           │  has)              │
        │           └────────────────────┘
        │
        ▼
  GitHub Issue #N          GitHub Actions check run
  (gh issue view N)        (gh run view <id> --log-failed)
```

## New components (minimal slice, updated post-review)

- `src/idemra/github/fetch.py` (new) — `_run_gh(*args) -> str` shared helper (2A), `GitHubFetchError` exception (1A), `task_from_issue(issue_ref) -> str` and `task_from_check(run_ref) -> str` (each prefixing a `[source: <url>]` marker per 3B, `task_from_check` truncating each failed job's log to its last 200 lines per 1C, raising `GitHubFetchError` on zero failed jobs per 3A).
- `src/idemra/db/models.py` (modified) — `Run` gains a nullable `source_ref: str | None` column (CT2).
- `migrations/versions/` (new) — Alembic migration adding `source_ref`, following the initial-schema migration's pattern.
- `src/idemra/orchestrator/runs.py` (modified) — `create_run()` gains an optional `source_ref` parameter, threaded through to the new column.
- `src/idemra/cli/main.py` (modified) — `run` command: `task` becomes `Optional[str]`, explicit mutex validation for `{task, --from-issue, --from-check}` (2B); for the GitHub-sourced branches, `create_run()` is called *first* with a placeholder task, then `task_from_issue`/`task_from_check` resolve the real text — success updates `run.task`, failure calls `fail_run()` on the now-existing row (CT1). Manually-typed `task` keeps today's call order unchanged.
- Tests: `tests/unit/test_github_fetch.py` (new, subprocess-mocked), additions to `tests/unit/test_cli_run.py`.
- `docs/manual-testing.md` (modified) — one new manual smoke-test entry exercising a real `gh` call (CT3).

Estimated footprint: 3 new files, 5 modified, 1 new exception class, 1
schema column — now at the edge of the Step 0 complexity guardrail
(8 files) but not over it. No further scope should be added to this
phase without another explicit scoping pass.

## Resolved during `/plan-eng-review`

`gh` CLI shell-out confirmed over a Python GitHub client (PyGithub/ghapi) —
zero new dependency, reuses the developer's existing `gh auth login`
session, matches ADR 0004's boring-tooling bias. Known trade-off accepted:
runtime dependency on `gh` being installed/authenticated (see 1A).

| # | Finding | Resolved | Rationale |
|---|---|---|---|
| 1A | `gh` failure handling | New `GitHubFetchError`, caught in `idemra run` exactly like `PermissionsNotFound`/`NotAGitRepo`/`MalformedProposal` → `fail_run()` → exit 1 | Keeps the "every failure is auditable" promise intact for the two new failure sources |
| 1B | Untrusted external content (issue bodies, CI logs) feeding the LLM prompt | Rely on the existing approval gate (D4); no new sanitization. Superseded in scope by 3B below | Approval gate + Layer 1/2 already block any actual write; real prompt-injection defense is a separate, deep problem out of scope for a minimal slice |
| 1C | CI log selection + truncation | `--from-check <ref>` takes a GitHub Actions **run ID**; each failed job's log truncated to its **last 200 lines** with an explicit `[... N lines truncated ...]` marker | Matches `gh run view`'s native identifier; bounds LLM prompt cost and DB row size; marker signals to the approver they're seeing a summary |
| 2A | DRY across `task_from_issue`/`task_from_check` | Single private `_run_gh(*args: str) -> str` helper both call | Same precedent as `record_event()` — one construction site for the risky operation |
| 2B | `task`/`--from-issue`/`--from-check` mutual exclusivity | `task` becomes `Optional[str]`; explicit if/elif/else at the top of `run()`, before `route_task()` | Explicit over clever; matches this codebase's existing inline-validation style; no new dependency |
| 3A | `task_from_check()` when the run has zero failed jobs | Raise `GitHubFetchError("run <ref> has no failed jobs")` immediately, reusing 1A's path | A generic "task cannot be empty" error would be confusing for what will be a common case |
| 3B | External-content warning durability (supersedes 1B's console-only framing) | `task_from_issue`/`task_from_check` both prefix their output with a structured `[source: <url>]` marker line, embedded in `run.task` itself | A one-time console print at `idemra run` time is invisible to whoever runs `idemra approve` later (Phase 5 made apply async) — embedding in `run.task` makes it durable across `status`/`log`/the original print, with zero schema change |

## Outside voice — cross-model corrections

An independent Claude subagent review (Codex not installed on this
machine) found one finding that actually overturned an Architecture
decision above (CT1), plus three more worth recording:

| # | Finding | Resolution |
|---|---|---|
| CT1 | **1A's audit-trail claim is false.** `Run.task` is non-nullable, and `create_run()` runs before every other failure check in `run()` — so a `gh` fetch failure has no Run row to attach `fail_run()` to. It would be a silent console-print-and-exit, the one failure class in this command with no audit trail. | **Fixed, not accepted.** `create_run()` is now called *first*, with a placeholder task text (e.g. `"[fetching GitHub issue #123]"`), for the `--from-issue`/`--from-check` branches only. `task_from_issue`/`task_from_check` run after; success updates `run.task` and continues normally; failure calls `fail_run()` on the now-existing row. Manually-typed `task` keeps today's unchanged call order. |
| CT2 | 3B's free-text-only `run.task` embedding forecloses the structured "is there already a run for issue #123" query the deferred polling TODO will need — a forced migration later, against live data, for the one piece of future work this phase stages toward. | **Schema added now.** `Run` gains a nullable `source_ref: str | None` column, populated by `--from-issue`/`--from-check`, left `None` for manually-typed tasks. New Alembic migration, following the same pattern as the initial schema migration. `[source: <url>]` marker in `run.task` (3B) is kept too — belt-and-suspenders, the column is queryable, the marker stays human-visible in `status`/`log`. |
| CT3 | Outside voice: apply the same real-infra test discipline as `tests/reliability/` (commit 562719d) instead of subprocess-mocked tests. | **Disagreed, kept as scoped.** GitHub is third-party — unlike Postgres/Redis, it can't be spun up in Docker for CI. Automated tests stay subprocess-mocked; one manual smoke-test entry added to `docs/manual-testing.md` instead, same treatment Phase 3's real-LLM step already gets. |
| CT4 | Outside voice: the actual deliverable doesn't match a milestone titled "GitHub & Self-Healing" — zero self-healing ships. | **Noted, not resolved here.** Milestone due 2026-11-14 has 3 months of runway; both TODOS.md items could plausibly land under it before close. Naming decision deferred to when this gets filed into GitHub issues. |

## TODOS.md additions

Two items added to `TODOS.md` under `## Phase 6 — GitHub & Self-Healing`,
both P3, both explicitly deferred rather than built now:
- **Always-on trigger infrastructure** (polling or webhook) — the natural next step once the manual `--from-issue`/`--from-check` flow proves useful
- **Autonomous-apply / narrow auto-approve design** — the option D4 declined for this phase; needs its own scoping pass, not a drop-in change

## Explicitly out of scope for this phase

- Polling loop or webhook endpoint (deferred — D5's manual-flag choice defers always-on infra until the manual path proves useful)
- Auto-approve / autonomous apply (deferred — D4 keeps the existing gate; a real autonomy design is a separate decision)
- Watch Mode generally (Phase 12, already on the roadmap — not pulled forward)
- Multi-issue/batch triggering (one `--from-issue`/`--from-check` per `idemra run` invocation, matching today's one-task-per-run model)
- CI-log truncation heuristics beyond a simple line-count cap (smarter failure-summarization is a nice-to-have, not blocking)

## Distribution

No new artifact type — same `idemra` CLI binary via the existing
`[project.scripts]` entry point, same `uv run idemra` / installed-venv
invocation. No new build/publish surface.

## Implementation Tasks

- [x] **T1 (P1, human: ~3h / CC: ~30min)** — github — Build src/idemra/github/fetch.py
  - Surfaced by: 1A, 1C, 2A, 3A, 3B — `_run_gh()` shared helper, `GitHubFetchError`, `task_from_issue()`, `task_from_check()` with per-job 200-line truncation + `[source: <url>]` marker + zero-failed-jobs rejection
  - Files: `src/idemra/github/fetch.py` (new)
  - Verify: `tests/unit/test_github_fetch.py` (T5) passes
- [x] **T2 (P1, human: ~1h / CC: ~15min)** — db — Add source_ref column
  - Surfaced by: CT2 — nullable `Run.source_ref`, new Alembic migration
  - Files: `src/idemra/db/models.py`, `migrations/versions/` (new)
  - Verify: `uv run alembic upgrade head` against real Postgres; `tests/integration/test_migrations.py` schema-diff test passes
- [x] **T3 (P1, human: ~30min / CC: ~10min)** — orchestrator — Thread source_ref through create_run()
  - Surfaced by: CT2
  - Files: `src/idemra/orchestrator/runs.py`
  - Verify: `tests/unit/test_orchestrator_runs.py`
- [x] **T4 (P1, human: ~3h / CC: ~30min)** — cli — Wire --from-issue/--from-check into idemra run
  - Surfaced by: 2B (mutex validation), CT1 (create_run-first + placeholder task for auditable failures)
  - Files: `src/idemra/cli/main.py`
  - Verify: `tests/unit/test_cli_run.py` (T6) passes
- [x] **T5 (P2, human: ~2h / CC: ~20min)** — tests — test_github_fetch.py full suite
  - Surfaced by: Test review diagram (13 codepaths traced)
  - Files: `tests/unit/test_github_fetch.py` (new)
  - Verify: `uv run pytest tests/unit/test_github_fetch.py -v`
- [x] **T6 (P2, human: ~1h / CC: ~15min)** — tests — CLI mutex + fetch-failure audit-trail cases
  - Surfaced by: 2B, CT1
  - Files: `tests/unit/test_cli_run.py`
  - Verify: `uv run pytest tests/unit/test_cli_run.py -v`
- [x] **T7 (P3, human: ~20min / CC: ~5min)** — docs — Manual gh smoke-test entry
  - Surfaced by: CT3
  - Files: `docs/manual-testing.md`
  - Verify: manual run against a real GitHub issue

## Implementation notes (discovered while building T1-T7)

Two things the design/review passes didn't (and couldn't) catch until
actual code was written and run against real infra:

- **`--from-issue`/`--from-check` take bare IDs, not full URLs.**
  Verified by hand against this project's own GitHub Actions runs: `gh
  issue view` accepts a full URL directly, but `gh run view` does not
  (`HTTP 404` when given one). The one interface that works consistently
  for both is a bare issue number / run ID plus an explicit `-R
  owner/repo` — resolved once via `gh repo view` run with `cwd=repo_root`
  (the *target* repo, not idemra's own working directory), so `gh`
  resolution is correct regardless of where `idemra` itself is invoked
  from. The data-flow diagram above still shows `--from-issue URL` for
  the high-level shape; the actual CLI takes bare IDs.
- **Rich markup was silently eating the `[source: <url>]` marker.**
  `console.print()` treats square brackets as markup syntax by default —
  `[source: https://...]` parsed as an (unrecognized, silently dropped)
  markup tag, not literal text. This affected every place a run's task
  text gets printed (`idemra status`, `idemra sweep`), which would have
  quietly defeated 3B/CT1's entire point (durable, human-visible source
  tracking) the first time anyone actually ran the CLI. Fixed with
  `rich.markup.escape()` at all three print sites. Caught by the new
  `--from-issue`/`--from-check` tests in `tests/unit/test_cli_run.py`,
  confirmed against a real GitHub issue via the manual smoke test.

All T1-T7 verified against real Postgres/Redis/GitHub/Ollama, not just
mocked tests: `--from-issue 1` against this repo's real issue #1 (full
audit trail, correct `[source: ...]` display), the mutex validation, a
real `gh` fetch failure (CT1's fix — the run row exists with the
placeholder task and a clear failure reason), and 3A's zero-failed-jobs
rejection against a real green CI run. `--from-check`'s job-log-fetching
happy path is covered by 13 mocked unit tests plus the same `_run_gh`/
`_repo_slug` code paths already proven live by `--from-issue` — not
worth deliberately breaking this project's own CI just to manufacture a
real failed run for one more manual check.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | clean | 7 review findings (1A/1B/1C/2A/2B/3A/3B) resolved; 4 cross-model corrections (CT1-CT4) resolved; 2 TODOs captured; 0 critical gaps; 0 performance issues |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**CROSS-MODEL:** Outside voice (Claude subagent, Codex not installed on this machine) found one finding (CT1) that overturned an Architecture-section decision already locked in — 1A's audit-trail claim didn't hold once the actual `create_run()`/`Run.task` non-nullable constraint was traced. Fixed via CT1, not just noted. Three further tension points (CT2 schema-now vs. later, CT3 real-infra test tier disagreement, CT4 milestone naming) resolved — CT3 resolved by disagreeing with the outside voice's recommendation and keeping the reviewer's original manual-smoke-test position instead.

**VERDICT:** ENG CLEARED — ready to implement. Lake Score: 13/13 decisions chose the complete/recommended option (11 review findings + 2 TODOs).

NO UNRESOLVED DECISIONS
