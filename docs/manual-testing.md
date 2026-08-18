# Manual testing — phase by phase

A walkthrough of the real `idemra` CLI against real Postgres/Redis, one
phase at a time. This is not a substitute for `uv run pytest` — it exists
for the failure modes automated tests structurally can't cover on their
own (see `tests/reliability/`'s docstring for why that tier exists) and
for sanity-checking a change against the actual CLI before it ships.

Every command below assumes it's run from the idemra repo root (the
`uv`-managed venv lives there) against a separate scratch target repo —
never test against the idemra repo itself.

## 0. One-time setup

```bash
cd ~/Developer/ai-projects/idemra
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml ps        # postgres, redis, qdrant all "Up"
uv sync --all-groups
uv run alembic upgrade head
```

No env vars needed below — `idemra` defaults to
`postgresql+psycopg://idemra:idemra@localhost:5433/idemra` and
`redis://localhost:6379/0`, matching `docker/docker-compose.yml`'s ports.

Create a scratch target repo to run Idemra against:

```bash
export TARGET=/tmp/idemra-manual-test
rm -rf "$TARGET" && mkdir -p "$TARGET" && cd "$TARGET"
git init -q -b main
git config user.email "you@example.com"; git config user.name "you"
printf 'def add(a, b):\n    return a + b\n' > calc.py
git add calc.py && git commit -q -m "initial"
cd ~/Developer/ai-projects/idemra    # idemra CLI must run from here
```

## Phase 1 — Skeleton & Permissions

```bash
uv run idemra init "$TARGET"
```
✅ Pass: prints `Initialized .../.idemra`, lists `permissions.yml`,
`outcomes.yml`, `mcp.yml`, `web_search.yml`, `.gitignore` as created.

```bash
uv run idemra config "$TARGET"
```
✅ Pass: `approval_required: ['write', 'delete', 'git_push']`,
`denied_paths: []`

```bash
cat "$TARGET/.idemra/permissions.yml"
```
✅ Pass: matches the config output above, human-readable YAML.

Idempotency check — re-run init:

```bash
uv run idemra init "$TARGET"
```
✅ Pass: `.idemra already exists — leaving it untouched.` (no overwrite,
exit 0)

## Phase 2 — World Model

```bash
uv run idemra index "$TARGET"
```
✅ Pass: `World model built`, `1 files -> .idemra/brain/snapshot.json`,
`1 symbols -> .idemra/brain/symbols.json`

```bash
cat "$TARGET/.idemra/brain/snapshot.json"
cat "$TARGET/.idemra/brain/symbols.json"
```
✅ Pass: snapshot lists `calc.py` with git metadata (clean tree); symbols
lists the `add` function.

## Phase 4 — Master Agent & Routing

Tested before Phase 3 since routing runs first inside `idemra run`, and
these two rejection paths are free/instant — rejected before any LLM
call.

```bash
uv run idemra run "$TARGET" ""
echo "exit code: $?"
```
✅ Pass: `Run <uuid> rejected by router: task cannot be empty`, exit code
1. Confirm it's still auditable:

```bash
uv run idemra status <uuid-from-above>
uv run idemra log <uuid-from-above>
```
✅ Pass: status=`failed`; log shows a `routing_decision` event with
`accepted: false` before the `status_changed: failed` event.

```bash
uv run idemra run /tmp/does-not-exist "add a function"
echo "exit code: $?"
```
✅ Pass: `rejected by router: /tmp/does-not-exist does not exist`, exit
code 1.

## Phase 3 — First Agent & LLM

Needs a real LLM. Pick one:

**Option A — local Ollama** (one-time ~4.7GB pull):
```bash
ollama pull qwen2.5-coder:7b
```

**Option B — Claude cloud fallback** (don't run Ollama, or leave it
without the model pulled; incurs real API cost):
```bash
export ANTHROPIC_API_KEY=sk-...
```

Then, either way:

```bash
uv run idemra run "$TARGET" "add a subtract(a, b) function to calc.py"
```
✅ Pass: `Run <uuid> created, awaiting approval (1 file(s) proposed)`.
Note the run id:

```bash
export RUN_ID=<uuid-from-output>
```

```bash
uv run idemra status "$RUN_ID"
```
✅ Pass: `status=awaiting_approval`

```bash
uv run idemra log "$RUN_ID"
```
✅ Pass: events in order — `routing_decision` (accepted:true) →
`status_changed:running` → `change_proposed` (contains the actual
proposed `calc.py` content) → `status_changed:awaiting_approval`.
Nothing written to disk yet:

```bash
cat "$TARGET/calc.py"   # should NOT contain subtract() yet
```

Malformed-proposal / permission-violation paths are covered by the
automated suite (`tests/unit/test_cli_run.py`) — not worth burning LLM
calls to reproduce manually unless you're debugging something specific
there.

## Phase 5 — Background Execution

```bash
uv run idemra approve "$RUN_ID"
```
✅ Pass: `Approved run ... — queued for apply`, `(needs a running idemra
worker)`.

```bash
uv run idemra status "$RUN_ID"    # status=queued
cat "$TARGET/calc.py"             # still unchanged — proves apply is async
```

```bash
uv run idemra worker --burst
```
✅ Pass: worker log shows it picked up `apply_run_job(<RUN_ID>)` and
completed it.

```bash
uv run idemra status "$RUN_ID"    # status=completed
cat "$TARGET/calc.py"             # now contains subtract()
uv run idemra log "$RUN_ID"       # files_applied, then status_changed:completed
uv run idemra replay "$RUN_ID"    # reconstructed status=completed, matches status above
```

**Idempotent retry check:**

```bash
uv run idemra approve "$RUN_ID"
```
✅ Pass: `Approved run ... (already completed — nothing more to queue)`
— no re-queue, no duplicate apply.

**Reject path** (repeat the Phase 3 `idemra run` command above to get a
second `RUN_ID2`, then):

```bash
uv run idemra reject "$RUN_ID2" --reason "not needed"
uv run idemra status "$RUN_ID2"   # status=rejected
cat "$TARGET/calc.py"             # unchanged
```

**Sweep / TTL expiry** — the CLI's 24h TTL won't expire live, so seed a
pre-expired approval directly against the real DB:

```bash
uv run python -c "
from datetime import timedelta
from idemra.db.session import session_scope
from idemra.orchestrator.runs import create_run, request_approval
with session_scope() as s:
    run = create_run(s, '$TARGET', 'sweep test')
    request_approval(s, run, 'write', timedelta(seconds=-1))
    print(run.id)
"
```

```bash
uv run idemra sweep
```
✅ Pass: `Expired 1 approval(s), run(s) marked stale:` listing the run id
printed above.

```bash
uv run idemra status <that-run-id>   # status=stale
```

## Cleanup

```bash
uv run idemra worker --burst   # drain anything left in the queue
docker compose -f docker/docker-compose.yml down   # or leave up if continuing work
rm -rf /tmp/idemra-manual-test
```
