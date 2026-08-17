"""session_scope()'s commit/rollback semantics.

typer.Exit is CLI control flow, not a transaction failure — it subclasses
Exception (via RuntimeError), so a naive `except Exception: rollback`
would silently discard every write a command made before choosing its
exit code. This was a real, previously-undetected bug: every fail_run()
followed by `raise typer.Exit(code=1)` inside session_scope() had its
write rolled back, all the way back through Phase 3.
"""

from pathlib import Path

import pytest
import typer
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from idemra.db.models import Base, Run
from idemra.db.session import session_scope


@pytest.fixture(autouse=True)
def _db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("IDEMRA_DATABASE_URL", f"sqlite:///{db_path}")
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)


def _count_runs() -> int:
    from idemra.db.session import get_engine

    session = sessionmaker(bind=get_engine())()
    try:
        return session.query(Run).count()
    finally:
        session.close()


def test_writes_before_typer_exit_are_committed_not_rolled_back() -> None:
    with pytest.raises(typer.Exit), session_scope() as session:
        session.add(Run(repo="/tmp/repo", task="do something"))
        session.flush()
        raise typer.Exit(code=1)

    assert _count_runs() == 1  # the write must survive an intentional CLI exit


def test_writes_before_a_real_exception_are_rolled_back() -> None:
    with pytest.raises(RuntimeError), session_scope() as session:
        session.add(Run(repo="/tmp/repo", task="do something"))
        session.flush()
        raise RuntimeError("something actually went wrong")

    assert _count_runs() == 0  # genuine failures still roll back


def test_successful_block_commits() -> None:
    with session_scope() as session:
        session.add(Run(repo="/tmp/repo", task="do something"))

    assert _count_runs() == 1
