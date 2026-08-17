"""Project-wide test safety nets.

No automated test is meant to touch a real Redis instance — RQ integration
is verified only in the manual live-infra smoke test (same treatment
Postgres gets from the per-test IDEMRA_DATABASE_URL override). Unlike
Postgres, nothing makes that isolation automatic: a test that forgets to
monkeypatch enqueue_apply would otherwise silently push a real job onto
whatever Redis happens to be running in dev — which is exactly what
happened once during Phase 5 development, leaking jobs with run_ids that
only ever existed in an ephemeral per-test SQLite file. Pointing
IDEMRA_REDIS_URL at an address nothing listens on turns that mistake into
an immediate, loud connection failure instead of a silent leak.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_real_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IDEMRA_REDIS_URL", "redis://localhost:1/0")
