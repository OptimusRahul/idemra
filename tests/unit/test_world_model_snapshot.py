"""World model snapshot builder — file tree + git metadata, Layer-1-filtered.

Layer 1 must exclude denied paths even when git has tracked them (e.g. a
secret committed by accident): the snapshot is a second, independent
enforcement point, not a rubber stamp on whatever git happens to track.
"""

import json
import subprocess
from pathlib import Path

import pytest

from idemra.world_model.snapshot import NotAGitRepo, build_snapshot, write_snapshot


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('hi')\n")
    (repo / "README.md").write_text("# repo\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def test_build_snapshot_lists_tracked_files_with_size_and_language(git_repo: Path) -> None:
    snapshot = build_snapshot(git_repo)

    files = {f.path: f for f in snapshot.files}
    assert files["src/main.py"].language == "python"
    assert files["src/main.py"].size_bytes == len("print('hi')\n")
    assert files["README.md"].language == "markdown"


def test_build_snapshot_excludes_untracked_files(git_repo: Path) -> None:
    (git_repo / "scratch.txt").write_text("not tracked")

    snapshot = build_snapshot(git_repo)

    assert "scratch.txt" not in {f.path for f in snapshot.files}


def test_build_snapshot_excludes_layer1_denied_paths_even_if_tracked(git_repo: Path) -> None:
    (git_repo / ".env").write_text("SECRET=1\n")
    _git(git_repo, "add", "-f", ".env")
    _git(git_repo, "commit", "-q", "-m", "oops, committed a secret")

    snapshot = build_snapshot(git_repo)

    assert ".env" not in {f.path for f in snapshot.files}


def test_build_snapshot_captures_git_metadata(git_repo: Path) -> None:
    snapshot = build_snapshot(git_repo)

    assert snapshot.git.branch == "main"
    assert len(snapshot.git.commit) == 40
    assert snapshot.git.dirty is False


def test_build_snapshot_detects_dirty_working_tree(git_repo: Path) -> None:
    (git_repo / "README.md").write_text("# repo\n\nchanged\n")

    snapshot = build_snapshot(git_repo)

    assert snapshot.git.dirty is True


def test_build_snapshot_raises_for_non_git_repo(tmp_path: Path) -> None:
    with pytest.raises(NotAGitRepo):
        build_snapshot(tmp_path)


def test_write_snapshot_produces_valid_json_in_brain_dir(git_repo: Path) -> None:
    snapshot = build_snapshot(git_repo)

    path = write_snapshot(snapshot, git_repo)

    assert path == git_repo / ".idemra" / "brain" / "snapshot.json"
    data = json.loads(path.read_text())
    assert data["git"]["branch"] == "main"
    assert {f["path"] for f in data["files"]} == {"src/main.py", "README.md"}


def test_write_snapshot_is_idempotent_on_rerun(git_repo: Path) -> None:
    write_snapshot(build_snapshot(git_repo), git_repo)
    (git_repo / "src" / "extra.py").write_text("x = 1\n")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "add extra")

    path = write_snapshot(build_snapshot(git_repo), git_repo)

    data = json.loads(path.read_text())
    assert "src/extra.py" in {f["path"] for f in data["files"]}
