"""idemra run / idemra approve wiring — end-to-end via CliRunner.

No real LLM call anywhere: llm_complete is monkeypatched at the CLI
module boundary (idemra.cli.main.llm_complete) with a fake.
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


def test_approve_after_run_writes_the_file(db, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "idemra.cli.main.llm_complete", _fake_complete_writing("new.py", "x = 1\n")
    )
    run_result = runner.invoke(app, ["run", str(repo), "add a file"])
    run_id = _extract_run_id(run_result.stdout)

    approve_result = runner.invoke(app, ["approve", run_id])

    assert approve_result.exit_code == 0
    assert "completed" in approve_result.stdout
    assert (repo / "new.py").read_text() == "x = 1\n"


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


def test_run_auto_applies_when_write_not_in_approval_required(
    db, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / ".idemra" / "permissions.yml").write_text("approval_required: []\ndenied_paths: []\n")
    monkeypatch.setattr(
        "idemra.cli.main.llm_complete", _fake_complete_writing("new.py", "x = 1\n")
    )

    result = runner.invoke(app, ["run", str(repo), "add a file"])

    assert result.exit_code == 0
    assert "completed" in result.stdout
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
    run_result = runner.invoke(app, ["run", str(repo), "add a file"])
    run_id = _extract_run_id(run_result.stdout)
    runner.invoke(app, ["approve", run_id])

    second = runner.invoke(app, ["approve", run_id])

    assert second.exit_code == 0
    assert "already completed" in second.stdout
