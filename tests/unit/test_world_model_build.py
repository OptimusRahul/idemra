"""Combined world-model build — snapshot + symbol index in one pass.

The idempotency requirement (issue #8's done-when bar) is the point of
these tests: re-running after files are added/removed must never leave
stale entries from a prior run sitting alongside the fresh ones.
"""

import json
import subprocess
from pathlib import Path

import pytest

from idemra.world_model.build import build_world_model


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    (repo / "old.py").write_text("def stale():\n    pass\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def test_build_world_model_writes_both_artifacts(git_repo: Path) -> None:
    result = build_world_model(git_repo)

    assert result.snapshot_path.exists()
    assert result.symbols_path.exists()
    assert any(s.name == "stale" for s in result.symbols)


def test_build_world_model_is_idempotent_no_stale_entries(git_repo: Path) -> None:
    build_world_model(git_repo)

    (git_repo / "old.py").unlink()
    _git(git_repo, "rm", "-q", "old.py")
    (git_repo / "new.py").write_text("def fresh():\n    pass\n")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "swap old.py for new.py")

    result = build_world_model(git_repo)

    snapshot_data = json.loads(result.snapshot_path.read_text())
    symbol_data = json.loads(result.symbols_path.read_text())

    snapshot_paths = {f["path"] for f in snapshot_data["files"]}
    symbol_names = {s["name"] for s in symbol_data}

    assert "old.py" not in snapshot_paths
    assert "new.py" in snapshot_paths
    assert "stale" not in symbol_names
    assert "fresh" in symbol_names
