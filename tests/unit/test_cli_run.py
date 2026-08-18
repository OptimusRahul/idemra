"""idemra run / idemra approve wiring — end-to-end via CliRunner.

No real LLM call anywhere: llm_complete is monkeypatched at the CLI
module boundary (idemra.cli.main.llm_complete) with a fake. Applying is
now queued, not synchronous — enqueue_apply is monkeypatched per-test
(never autouse) so both states are directly provable: a spy proves
idemra approve/run returns without applying (the real async behavior),
and a separate monkeypatch to apply_run_job simulates an instantly
available worker to prove the job itself does the right thing. Real RQ
enqueue/dequeue mechanics are verified only in the manual live-infra
smoke test — same treatment this suite gives Postgres (SQLite stands in
here; nothing here touches real Redis).
"""

import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from idemra.cli.main import app
from idemra.db.models import Base
from idemra.queue.jobs import apply_run_job

runner = CliRunner()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_git_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "main.py").write_text("def hello():\n    pass\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")


def _fake_complete_writing(path: str, content: str):
    payload = json.dumps([{"path": path, "content": content}])
    return lambda prompt: payload


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("IDEMRA_DATABASE_URL", f"sqlite:///{db_path}")
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)
    (repo_path / ".idemra").mkdir()
    (repo_path / ".idemra" / "permissions.yml").write_text(
        "approval_required:\n  - write\ndenied_paths: []\n"
    )
    return repo_path


def _extract_run_id(stdout: str) -> str:
    # "Run <uuid> created, awaiting approval ..." / "Run <uuid> completed ..."
    return stdout.split("Run ")[1].split()[0]


def test_run_creates_proposal_and_does_not_block(db, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "idemra.cli.main.llm_complete", _fake_complete_writing("new.py", "x = 1\n")
    )

    result = runner.invoke(app, ["run", str(repo), "add a file"])

    assert result.exit_code == 0
    assert "awaiting approval" in result.stdout
    assert not (repo / "new.py").exists()  # non-blocking — nothing written yet


def test_approve_queues_without_applying_synchronously(db, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "idemra.cli.main.llm_complete", _fake_complete_writing("new.py", "x = 1\n")
    )
    calls = []
    monkeypatch.setattr("idemra.cli.main.enqueue_apply", lambda run_id: calls.append(run_id))
    run_result = runner.invoke(app, ["run", str(repo), "add a file"])
    run_id = _extract_run_id(run_result.stdout)

    approve_result = runner.invoke(app, ["approve", run_id])

    assert approve_result.exit_code == 0
    assert "queued" in approve_result.stdout
    assert calls == [run_id]
    assert not (repo / "new.py").exists()  # nothing applied without a worker

    status_result = runner.invoke(app, ["status", run_id])
    assert "queued" in status_result.stdout


def test_approve_then_worker_applies_the_change(db, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "idemra.cli.main.llm_complete", _fake_complete_writing("new.py", "x = 1\n")
    )
    # enqueue_apply is a no-op during the CLI calls — a worker is a
    # separate process that only ever sees a job after the enqueuing
    # transaction has committed, so apply_run_job is invoked as an
    # explicit separate step below, not inline during approve().
    monkeypatch.setattr("idemra.cli.main.enqueue_apply", lambda run_id: None)
    run_result = runner.invoke(app, ["run", str(repo), "add a file"])
    run_id = _extract_run_id(run_result.stdout)
    approve_result = runner.invoke(app, ["approve", run_id])
    assert approve_result.exit_code == 0

    apply_run_job(run_id)  # simulates `idemra worker` picking the job up

    assert (repo / "new.py").read_text() == "x = 1\n"
    status_result = runner.invoke(app, ["status", run_id])
    assert "completed" in status_result.stdout


def test_reject_after_run_writes_nothing(db, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "idemra.cli.main.llm_complete", _fake_complete_writing("new.py", "x = 1\n")
    )
    run_result = runner.invoke(app, ["run", str(repo), "add a file"])
    run_id = _extract_run_id(run_result.stdout)

    reject_result = runner.invoke(app, ["reject", run_id])

    assert reject_result.exit_code == 0
    assert not (repo / "new.py").exists()


def test_run_fails_closed_on_layer1_denied_path(db, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "idemra.cli.main.llm_complete", _fake_complete_writing(".env", "SECRET=1\n")
    )

    result = runner.invoke(app, ["run", str(repo), "steal secrets"])

    assert result.exit_code == 1
    assert "failed" in result.stdout
    assert not (repo / ".env").exists()


def test_run_fails_closed_on_layer2_denied_path(db, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / ".idemra" / "permissions.yml").write_text(
        "approval_required:\n  - write\ndenied_paths:\n  - 'secrets/*.json'\n"
    )
    monkeypatch.setattr(
        "idemra.cli.main.llm_complete", _fake_complete_writing("secrets/creds.json", "{}\n")
    )

    result = runner.invoke(app, ["run", str(repo), "write a denied config"])

    assert result.exit_code == 1
    assert not (repo / "secrets" / "creds.json").exists()


def test_run_auto_queues_when_write_not_in_approval_required(
    db, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / ".idemra" / "permissions.yml").write_text("approval_required: []\ndenied_paths: []\n")
    monkeypatch.setattr(
        "idemra.cli.main.llm_complete", _fake_complete_writing("new.py", "x = 1\n")
    )
    calls = []
    monkeypatch.setattr("idemra.cli.main.enqueue_apply", lambda run_id: calls.append(run_id))

    result = runner.invoke(app, ["run", str(repo), "add a file"])

    assert result.exit_code == 0
    assert "queued" in result.stdout
    assert len(calls) == 1
    assert not (repo / "new.py").exists()  # nothing applied without a worker


def test_run_auto_apply_then_worker_applies_the_change(
    db, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / ".idemra" / "permissions.yml").write_text("approval_required: []\ndenied_paths: []\n")
    monkeypatch.setattr(
        "idemra.cli.main.llm_complete", _fake_complete_writing("new.py", "x = 1\n")
    )
    monkeypatch.setattr("idemra.cli.main.enqueue_apply", lambda run_id: None)

    result = runner.invoke(app, ["run", str(repo), "add a file"])
    assert result.exit_code == 0
    run_id = _extract_run_id(result.stdout)

    apply_run_job(run_id)  # simulates `idemra worker` picking the job up

    assert (repo / "new.py").read_text() == "x = 1\n"


def test_run_fails_on_malformed_llm_response(db, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("idemra.cli.main.llm_complete", lambda prompt: "not valid json")

    result = runner.invoke(app, ["run", str(repo), "add a file"])

    assert result.exit_code == 1
    assert "failed" in result.stdout


def test_retried_approve_is_idempotent(db, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "idemra.cli.main.llm_complete", _fake_complete_writing("new.py", "x = 1\n")
    )
    monkeypatch.setattr("idemra.cli.main.enqueue_apply", lambda run_id: None)
    run_result = runner.invoke(app, ["run", str(repo), "add a file"])
    run_id = _extract_run_id(run_result.stdout)
    runner.invoke(app, ["approve", run_id])
    apply_run_job(run_id)  # simulates `idemra worker` completing the job

    second = runner.invoke(app, ["approve", run_id])

    assert second.exit_code == 0
    assert "already completed" in second.stdout


def test_run_rejects_empty_task_before_building_world_model(db, repo: Path) -> None:
    result = runner.invoke(app, ["run", str(repo), ""])

    assert result.exit_code == 1
    assert "rejected" in result.stdout
    assert not (repo / ".idemra" / "brain").exists()  # world model was never built

    run_id = _extract_run_id(result.stdout)
    status_result = runner.invoke(app, ["status", run_id])
    assert status_result.exit_code == 0
    assert "failed" in status_result.stdout  # the run row exists — auditable, not silently dropped


def test_run_rejects_nonexistent_repo(db, tmp_path: Path) -> None:
    missing_repo = tmp_path / "does-not-exist"

    result = runner.invoke(app, ["run", str(missing_repo), "add a file"])

    assert result.exit_code == 1
    assert "rejected" in result.stdout

    run_id = _extract_run_id(result.stdout)
    status_result = runner.invoke(app, ["status", run_id])
    assert status_result.exit_code == 0
    assert "failed" in status_result.stdout


def test_run_fails_when_permissions_missing_but_still_auditable(db, repo: Path) -> None:
    (repo / ".idemra" / "permissions.yml").unlink()

    result = runner.invoke(app, ["run", str(repo), "add a file"])

    assert result.exit_code == 1
    run_id = _extract_run_id(result.stdout)
    status_result = runner.invoke(app, ["status", run_id])
    assert status_result.exit_code == 0
    assert "failed" in status_result.stdout  # Phase 3 never created a row for this failure


def test_run_records_routing_decision_before_change_proposed(
    db, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "idemra.cli.main.llm_complete", _fake_complete_writing("new.py", "x = 1\n")
    )
    run_result = runner.invoke(app, ["run", str(repo), "add a file"])
    run_id = _extract_run_id(run_result.stdout)

    log_result = runner.invoke(app, ["log", run_id])

    lines_with_events = [line for line in log_result.stdout.splitlines() if "seq=" in line]
    assert "routing_decision" in lines_with_events[0]
    event_types_in_order = [line.split()[1] for line in lines_with_events]
    assert event_types_in_order.index("routing_decision") < event_types_in_order.index("change_proposed")


def test_run_with_from_issue_builds_task_via_github_fetch(
    db, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "idemra.cli.main.llm_complete", _fake_complete_writing("new.py", "x = 1\n")
    )
    monkeypatch.setattr(
        "idemra.cli.main.task_from_issue",
        lambda issue_ref, repo_root: f"[source: https://github.com/o/r/issues/{issue_ref}]\n\nfix the bug",
    )

    result = runner.invoke(app, ["run", str(repo), "--from-issue", "42"])

    assert result.exit_code == 0
    assert "awaiting approval" in result.stdout
    run_id = _extract_run_id(result.stdout)
    status_result = runner.invoke(app, ["status", run_id])
    # Rich wraps long lines at console width, so normalize whitespace
    # before checking for the marker rather than matching an exact substring.
    assert "[source: https://github.com/o/r/issues/42]" in " ".join(status_result.stdout.split())


def test_run_with_from_check_builds_task_via_github_fetch(
    db, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "idemra.cli.main.llm_complete", _fake_complete_writing("new.py", "x = 1\n")
    )
    monkeypatch.setattr(
        "idemra.cli.main.task_from_check",
        lambda run_ref, repo_root: f"[source: https://github.com/o/r/actions/runs/{run_ref}]\n\nfix the failure",
    )

    result = runner.invoke(app, ["run", str(repo), "--from-check", "999"])

    assert result.exit_code == 0
    run_id = _extract_run_id(result.stdout)
    status_result = runner.invoke(app, ["status", run_id])
    assert "[source: https://github.com/o/r/actions/runs/999]" in " ".join(status_result.stdout.split())


def test_run_rejects_when_task_and_from_issue_both_given(db, repo: Path) -> None:
    result = runner.invoke(app, ["run", str(repo), "add a file", "--from-issue", "42"])

    assert result.exit_code == 1
    assert "exactly one of" in result.stdout


def test_run_rejects_when_none_of_task_from_issue_from_check_given(db, repo: Path) -> None:
    result = runner.invoke(app, ["run", str(repo)])

    assert result.exit_code == 1
    assert "exactly one of" in result.stdout


def test_run_fails_run_and_exits_1_on_github_fetch_error(
    db, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CT1 fix, proven: a gh-fetch failure still has a Run row to
    record itself against — auditable via status/log, not a silent
    console-print-and-exit."""
    from idemra.github.fetch import GitHubFetchError

    def _raise(issue_ref: str, repo_root: Path) -> str:
        raise GitHubFetchError(f"issue {issue_ref} not found")

    monkeypatch.setattr("idemra.cli.main.task_from_issue", _raise)

    result = runner.invoke(app, ["run", str(repo), "--from-issue", "999"])

    assert result.exit_code == 1
    assert "failed" in result.stdout
    run_id = _extract_run_id(result.stdout)

    status_result = runner.invoke(app, ["status", run_id])
    assert status_result.exit_code == 0
    assert "failed" in status_result.stdout

    log_result = runner.invoke(app, ["log", run_id])
    assert "issue 999 not found" in log_result.stdout
