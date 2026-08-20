#!/usr/bin/env bash
# scripts/verify-phases.sh — full regression check, run after any code
# change: the automated test suite AND a live CLI walkthrough against real
# Postgres/Redis/GitHub, phase by phase. Automates docs/manual-testing.md
# rather than replacing it — read that doc for the "why" behind each step.
#
# Deliberately does NOT use `set -e`: a single failed check should not
# abort the run before every other phase has had a chance to report its
# own status. Every check is explicit; the script's own exit code is 0
# only if nothing failed (skips are fine).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- flags -----------------------------------------------------------
SKIP_LLM=0
for arg in "$@"; do
  case "$arg" in
    --skip-llm) SKIP_LLM=1 ;;
    -h|--help)
      echo "Usage: $0 [--skip-llm]"
      echo "  --skip-llm  Force-skip Phase 3's real LLM call even if Ollama/Claude is available."
      exit 0
      ;;
  esac
done

# --- reporting ---------------------------------------------------------
PASS=0
FAIL=0
SKIPPED=0
FAILURES=()

pass() { echo "  ✅ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ❌ $1"; FAIL=$((FAIL + 1)); FAILURES+=("$1"); }
skip() { echo "  ⚠️  SKIP: $1"; SKIPPED=$((SKIPPED + 1)); }
section() { echo; echo "=== $1 ==="; }

check() {
  # check "label" "grep pattern" "$output"
  local label="$1" pattern="$2" output="$3"
  if echo "$output" | grep -qF "$pattern"; then
    pass "$label"
  else
    fail "$label — expected to find: $pattern"
  fi
}

echo "idemra phase verification — $(date '+%Y-%m-%d %H:%M:%S')"
echo "repo: $REPO_ROOT"

# --- 0. infrastructure -------------------------------------------------
section "0. Infrastructure"

docker compose -f docker/docker-compose.yml up -d >/dev/null 2>&1
PG_READY=0
for _ in $(seq 1 15); do
  if docker exec docker-postgres-1 pg_isready -U idemra >/dev/null 2>&1; then
    PG_READY=1
    break
  fi
  sleep 1
done
if [ "$PG_READY" = "1" ]; then
  pass "Postgres reachable"
else
  fail "Postgres not reachable after 15s — is docker/docker-compose.yml up?"
fi

if docker exec docker-redis-1 redis-cli ping 2>/dev/null | grep -q PONG; then
  pass "Redis reachable"
else
  fail "Redis not reachable"
fi

unset IDEMRA_DATABASE_URL IDEMRA_REDIS_URL FORCE_COLOR

if uv run alembic upgrade head >/dev/null 2>&1; then
  pass "alembic upgrade head"
else
  fail "alembic upgrade head"
fi

# --- 1. automated test suite --------------------------------------------
section "Automated test suite (unit + reliability + integration)"

if uv run ruff check . >/dev/null 2>&1; then
  pass "ruff lint"
else
  fail "ruff lint"
fi

PYTEST_OUTPUT="$(uv run pytest -q 2>&1)"
if echo "$PYTEST_OUTPUT" | tail -5 | grep -qE "^[0-9]+ passed" && ! echo "$PYTEST_OUTPUT" | grep -q "failed"; then
  SUMMARY_LINE="$(echo "$PYTEST_OUTPUT" | tail -1)"
  pass "pytest suite ($SUMMARY_LINE)"
else
  fail "pytest suite — see output below"
  echo "$PYTEST_OUTPUT" | tail -30
fi

# --- scratch target repo ------------------------------------------------
TARGET="$(mktemp -d)/idemra-verify-target"
mkdir -p "$TARGET"
cleanup() { rm -rf "$(dirname "$TARGET")"; }
trap cleanup EXIT

(
  cd "$TARGET"
  git init -q -b main
  git config user.email "verify@example.com"
  git config user.name "verify-phases"
  printf 'def add(a, b):\n    return a + b\n' > calc.py
  git add calc.py
  git commit -q -m "initial"
  git remote add origin git@github.com:OptimusRahul/idemra.git
)

run_idemra() {
  # Rich wraps long lines at console width, which can split a phrase
  # we're grepping for across two lines. Normalize whitespace so every
  # check() call is robust to wrapping, same fix applied to the pytest
  # assertions this script's checks mirror (tests/unit/test_cli_run.py).
  uv run idemra "$@" 2>&1 | tr '\n' ' ' | tr -s ' '
}
extract_run_id() {
  # "Run <uuid> created/rejected/failed ..." — first line only, robust to
  # Rich line-wrapping wrapping the rest of the message across lines.
  echo "$1" | tr '\n' ' ' | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1
}

# --- Phase 1 — Skeleton & Permissions -----------------------------------
section "Phase 1 — Skeleton & Permissions"

OUT="$(run_idemra init "$TARGET")"
check "idemra init creates .idemra/" "Initialized" "$OUT"

OUT="$(run_idemra config "$TARGET")"
check "idemra config reads permissions.yml" "approval_required" "$OUT"

if [ -f "$TARGET/.idemra/permissions.yml" ]; then
  pass "permissions.yml written to disk"
else
  fail "permissions.yml missing on disk"
fi

OUT="$(run_idemra init "$TARGET")"
check "idemra init is idempotent (re-run leaves it untouched)" "already exists" "$OUT"

# --- Phase 2 — World Model ------------------------------------------------
section "Phase 2 — World Model"

OUT="$(run_idemra index "$TARGET")"
check "idemra index builds the world model" "World model built" "$OUT"

if [ -f "$TARGET/.idemra/brain/snapshot.json" ] && [ -f "$TARGET/.idemra/brain/symbols.json" ]; then
  pass "snapshot.json and symbols.json written"
else
  fail "world model artifacts missing on disk"
fi

if grep -q '"add"' "$TARGET/.idemra/brain/symbols.json" 2>/dev/null; then
  pass "symbol index found the add() function"
else
  fail "symbol index did not find add() in calc.py"
fi

# --- Phase 4 — Master Agent & Routing -------------------------------------
# Tested before Phase 3: routing runs first inside `idemra run`, and these
# two rejection paths are free/instant — rejected before any LLM call.
section "Phase 4 — Master Agent & Routing"

OUT="$(run_idemra run "$TARGET" "")"
check "empty task is rejected by the router" "rejected by router: task cannot be empty" "$OUT"
RUN_ID="$(extract_run_id "$OUT")"
if [ -n "$RUN_ID" ]; then
  STATUS_OUT="$(run_idemra status "$RUN_ID")"
  check "rejected run is still auditable (status=failed)" "status=failed" "$STATUS_OUT"
  LOG_OUT="$(run_idemra log "$RUN_ID")"
  check "rejected run's routing_decision event was recorded" "routing_decision" "$LOG_OUT"
else
  fail "could not extract run id from empty-task rejection output"
fi

OUT="$(run_idemra run /tmp/idemra-verify-does-not-exist "add a function")"
check "nonexistent repo is rejected by the router" "does not exist" "$OUT"

# --- Phase 5 — Background Execution ----------------------------------
# Seeds a run+proposal+approval directly (bypassing the LLM, same as the
# manual smoke test) to exercise the real queue/worker/approve/sweep
# pipeline against real Postgres+Redis without needing Ollama/Claude.
section "Phase 5 — Background Execution"

SEED_RUN_ID="$(uv run python scripts/_seed_run.py --repo "$TARGET" \
  --task "verify-phases: add a subtract function" \
  --proposal-path calc.py \
  --proposal-content $'def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n' \
  --ttl-seconds 86400 2>/dev/null)"

if [ -n "$SEED_RUN_ID" ]; then
  pass "seeded a run+proposal+approval directly against real Postgres"
else
  fail "could not seed a run for Phase 5 checks — skipping the rest of this phase"
fi

if [ -n "$SEED_RUN_ID" ]; then
  OUT="$(run_idemra approve "$SEED_RUN_ID")"
  check "approve queues without applying synchronously" "queued for apply" "$OUT"

  STATUS_OUT="$(run_idemra status "$SEED_RUN_ID")"
  check "status=queued before a worker runs" "status=queued" "$STATUS_OUT"
  if grep -q "subtract" "$TARGET/calc.py" 2>/dev/null; then
    fail "file was written before a worker ran — apply is not actually async"
  else
    pass "file untouched before a worker ran (apply is genuinely async)"
  fi

  OUT="$(run_idemra worker --burst)"
  check "worker --burst picks up and completes the queued job" "$SEED_RUN_ID" "$OUT"

  STATUS_OUT="$(run_idemra status "$SEED_RUN_ID")"
  check "status=completed after the worker ran" "status=completed" "$STATUS_OUT"
  if grep -q "def subtract" "$TARGET/calc.py" 2>/dev/null; then
    pass "worker actually wrote the proposed change to disk"
  else
    fail "worker ran but calc.py was not updated"
  fi

  LOG_OUT="$(run_idemra log "$SEED_RUN_ID")"
  check "event log shows files_applied" "files_applied" "$LOG_OUT"

  REPLAY_OUT="$(run_idemra replay "$SEED_RUN_ID")"
  check "replay reconstructs the same status from the event log alone" "reconstructed status=completed" "$REPLAY_OUT"

  OUT="$(run_idemra approve "$SEED_RUN_ID")"
  check "retried approve on a completed run is idempotent (no re-queue)" "already completed" "$OUT"
fi

# reject path — separate seeded run
REJECT_RUN_ID="$(uv run python scripts/_seed_run.py --repo "$TARGET" \
  --task "verify-phases: reject path" \
  --proposal-path unwanted.py \
  --proposal-content $'x = 1\n' \
  --ttl-seconds 86400 2>/dev/null)"

if [ -n "$REJECT_RUN_ID" ]; then
  OUT="$(run_idemra reject "$REJECT_RUN_ID" --reason "verify-phases")"
  check "reject exits cleanly" "Rejected" "$OUT"
  if [ -f "$TARGET/unwanted.py" ]; then
    fail "rejected run's file was written anyway"
  else
    pass "rejected run wrote nothing to disk"
  fi
else
  fail "could not seed a run for the reject-path check"
fi

# sweep — a pre-expired approval
SWEEP_RUN_ID="$(uv run python scripts/_seed_run.py --repo "$TARGET" \
  --task "verify-phases: sweep target" \
  --ttl-seconds -1 2>/dev/null)"

if [ -n "$SWEEP_RUN_ID" ]; then
  OUT="$(run_idemra sweep)"
  check "sweep expires the past-TTL approval" "$SWEEP_RUN_ID" "$OUT"
  STATUS_OUT="$(run_idemra status "$SWEEP_RUN_ID")"
  check "swept run is marked stale" "status=stale" "$STATUS_OUT"
else
  fail "could not seed a run for the sweep check"
fi

# --- Phase 6 — GitHub & Self-Healing --------------------------------------
section "Phase 6 — GitHub & Self-Healing"

# Computed once and reused in Phase 3 below — `gh auth status` makes a
# real network/keychain round-trip, not worth paying for twice.
GH_AVAILABLE=0
if ! command -v gh >/dev/null 2>&1; then
  skip "gh CLI not installed — Phase 6 checks need it"
elif ! gh auth status >/dev/null 2>&1; then
  skip "gh CLI not authenticated (run 'gh auth login') — Phase 6 checks need it"
else
  GH_AVAILABLE=1
  OUT="$(run_idemra run "$TARGET" "some task" --from-issue 1)"
  check "mutex: task + --from-issue both given is rejected" "exactly one of" "$OUT"

  OUT="$(run_idemra run "$TARGET")"
  check "mutex: neither task nor a --from flag given is rejected" "exactly one of" "$OUT"

  # task_from_issue() called directly, no `idemra run` involved — proves
  # the real gh fetch mechanics (repo resolution, issue lookup, [source:
  # ...] marker) without paying for or depending on an LLM call, which
  # `idemra run --from-issue` would otherwise require. The full pipeline
  # (fetch -> route -> LLM -> propose) is checked separately below, gated
  # on an LLM actually being available.
  FETCH_OUT="$(uv run python -c "
from pathlib import Path
from idemra.github.fetch import task_from_issue
print(task_from_issue('1', Path('$TARGET')))
" 2>&1)"
  check "task_from_issue() fetches a real issue with the [source: ...] marker" \
    "[source: https://github.com/OptimusRahul/idemra/issues/1]" "$FETCH_OUT"

  OUT="$(run_idemra run "$TARGET" --from-issue 999999)"
  check "a real gh fetch failure is rejected with a clear message" "failed" "$OUT"
  BAD_RUN_ID="$(extract_run_id "$OUT")"
  if [ -n "$BAD_RUN_ID" ]; then
    STATUS_OUT="$(run_idemra status "$BAD_RUN_ID")"
    check "the CT1 fix: a fetch failure still produces an auditable run row" "status=failed" "$STATUS_OUT"
  else
    fail "could not extract run id from the gh-failure output — the CT1 fix may have regressed"
  fi

  GREEN_RUN_ID="$(gh run list -R OptimusRahul/idemra --status success --limit 1 --json databaseId -q '.[0].databaseId' 2>/dev/null)"
  if [ -n "$GREEN_RUN_ID" ]; then
    OUT="$(run_idemra run "$TARGET" --from-check "$GREEN_RUN_ID")"
    check "--from-check rejects a run with zero failed jobs" "has no failed jobs" "$OUT"
  else
    skip "could not find a successful CI run on GitHub to test the zero-failed-jobs case"
  fi
fi

# --- Phase 3 — First Agent & LLM ------------------------------------------
# The only phase needing a real LLM. Auto-detects Ollama/Claude; skips
# cleanly (not a failure) if neither is available, or if --skip-llm was
# passed. A real small local model occasionally returns invalid JSON —
# that's model-quality noise, not a code regression (the deterministic
# malformed-JSON parsing path already has its own pytest coverage in
# tests/unit/test_cli_run.py::test_run_fails_on_malformed_llm_response).
# This section's job is to prove the wiring reaches the LLM and comes
# back cleanly either way, not to grade the model's output.
section "Phase 3 — First Agent & LLM"

# Same env vars and defaults src/idemra/llm/router.py itself reads, so
# this detection can't silently drift out of sync with what idemra would
# actually try to call.
OLLAMA_URL="${IDEMRA_OLLAMA_URL:-http://localhost:11434}"
LOCAL_MODEL="${IDEMRA_LOCAL_MODEL:-ollama/qwen2.5-coder:7b}"
OLLAMA_MODEL_NAME="${LOCAL_MODEL#ollama/}"

LLM_AVAILABLE=0
LLM_WHY=""
if [ "$SKIP_LLM" = "1" ]; then
  LLM_WHY="--skip-llm passed"
elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  LLM_AVAILABLE=1
  LLM_WHY="ANTHROPIC_API_KEY is set"
elif curl -s -m 2 "$OLLAMA_URL/api/tags" 2>/dev/null | grep -qF "\"$OLLAMA_MODEL_NAME\""; then
  LLM_AVAILABLE=1
  LLM_WHY="Ollama reachable at $OLLAMA_URL with $OLLAMA_MODEL_NAME pulled"
else
  LLM_WHY="no ANTHROPIC_API_KEY and Ollama unreachable/model not pulled at $OLLAMA_URL"
fi

if [ "$LLM_AVAILABLE" = "1" ]; then
  echo "  (LLM available: $LLM_WHY)"

  OUT="$(run_idemra run "$TARGET" "add a multiply function to calc.py")"
  if echo "$OUT" | grep -qF "awaiting approval"; then
    pass "idemra run reaches the LLM and gets a valid proposal"
  elif echo "$OUT" | grep -qF "malformed proposal"; then
    pass "idemra run reaches the LLM (model returned invalid JSON this time — quality noise, not a code regression; see test_run_fails_on_malformed_llm_response for the deterministic version of this check)"
  else
    fail "idemra run with a real task neither succeeded nor failed cleanly — see output below"
    echo "$OUT"
  fi
  LLM_RUN_ID="$(extract_run_id "$OUT")"
  [ -n "$LLM_RUN_ID" ] && run_idemra reject "$LLM_RUN_ID" --reason "verify-phases" >/dev/null

  if [ "$GH_AVAILABLE" = "1" ]; then
    OUT="$(run_idemra run "$TARGET" --from-issue 1)"
    if echo "$OUT" | grep -qF "awaiting approval" || echo "$OUT" | grep -qF "malformed proposal"; then
      pass "--from-issue's full pipeline (fetch -> route -> LLM) runs end to end"
    else
      fail "--from-issue's full pipeline neither succeeded nor failed cleanly — see output below"
      echo "$OUT"
    fi
    GH_LLM_RUN_ID="$(extract_run_id "$OUT")"
    [ -n "$GH_LLM_RUN_ID" ] && run_idemra reject "$GH_LLM_RUN_ID" --reason "verify-phases" >/dev/null
  fi
else
  skip "Phase 3 and --from-issue's full pipeline ($LLM_WHY)"
fi

# --- summary -------------------------------------------------------------
section "Summary"
echo "  passed:  $PASS"
echo "  failed:  $FAIL"
echo "  skipped: $SKIPPED"

if [ "$FAIL" -gt 0 ]; then
  echo
  echo "Failures:"
  for f in "${FAILURES[@]}"; do
    echo "  - $f"
  done
  echo
  echo "❌ FAILED"
  exit 1
fi

echo
echo "✅ ALL PHASES VERIFIED"
exit 0
