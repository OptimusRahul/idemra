import subprocess
from pathlib import Path

from typer.testing import CliRunner

from idemra.cli.main import app

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


def test_index_builds_world_model(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    result = runner.invoke(app, ["index", str(repo)])

    assert result.exit_code == 0
    assert (repo / ".idemra" / "brain" / "snapshot.json").exists()
    assert (repo / ".idemra" / "brain" / "symbols.json").exists()
    assert "1 files" in result.stdout
    assert "hello" not in result.stdout  # summary output, not the full symbol list


def test_index_on_non_git_repo_fails(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    result = runner.invoke(app, ["index", str(not_a_repo)])

    assert result.exit_code == 1
