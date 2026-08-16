"""Repo structural snapshot — file tree + git metadata.

Walks git-tracked files only: this mirrors .gitignore automatically and
skips junk (__pycache__/, node_modules/, venvs) without a hardcoded
exclusion list. Every tracked path is still re-checked against Layer 1's
jail + secret-file denylist as a defense-in-depth backstop — git tracking
a file is not the same as Layer 1 permitting it (e.g. a secret committed
by accident).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from idemra.config.scaffold import idemra_dir
from idemra.permissions.layer1 import (
    PermissionViolation,
    assert_not_denied_path,
    assert_within_repo_root,
)

LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".sh": "shell",
    ".sql": "sql",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".toml": "toml",
}


class NotAGitRepo(Exception):
    pass


@dataclass(frozen=True)
class FileEntry:
    path: str  # posix, relative to repo_root
    size_bytes: int
    language: str | None


@dataclass(frozen=True)
class GitMetadata:
    branch: str
    commit: str
    dirty: bool


@dataclass(frozen=True)
class RepoSnapshot:
    repo_root: str
    git: GitMetadata
    files: list[FileEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "repo_root": self.repo_root,
            "git": {
                "branch": self.git.branch,
                "commit": self.git.commit,
                "dirty": self.git.dirty,
            },
            "files": [
                {"path": f.path, "size_bytes": f.size_bytes, "language": f.language}
                for f in self.files
            ],
        }


def _run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,  # returncode handled below to raise NotAGitRepo with context
    )
    if result.returncode != 0:
        raise NotAGitRepo(f"git {' '.join(args)} failed in {repo_root}: {result.stderr.strip()}")
    return result.stdout.strip()


def _git_metadata(repo_root: Path) -> GitMetadata:
    branch = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    commit = _run_git(repo_root, "rev-parse", "HEAD")
    dirty = bool(_run_git(repo_root, "status", "--porcelain"))
    return GitMetadata(branch=branch, commit=commit, dirty=dirty)


def _tracked_files(repo_root: Path) -> list[Path]:
    output = _run_git(repo_root, "ls-files")
    return [repo_root / line for line in output.splitlines() if line]


def build_snapshot(repo_root: Path) -> RepoSnapshot:
    """Build a structural snapshot of every Layer-1-permitted, git-tracked file."""
    repo_root = repo_root.resolve()
    git_meta = _git_metadata(repo_root)

    entries = []
    for path in _tracked_files(repo_root):
        assert_within_repo_root(path, repo_root)
        try:
            assert_not_denied_path(path)
        except PermissionViolation:
            continue  # Layer 1 denies this file — excluded from the world model, not an error
        if not path.is_file():
            continue  # tracked but absent locally (submodule gitlink, race with a delete)

        entries.append(
            FileEntry(
                path=path.relative_to(repo_root).as_posix(),
                size_bytes=path.stat().st_size,
                language=LANGUAGE_BY_EXTENSION.get(path.suffix.lower()),
            )
        )

    return RepoSnapshot(repo_root=str(repo_root), git=git_meta, files=entries)


def brain_dir(repo_root: Path) -> Path:
    return idemra_dir(repo_root) / "brain"


def write_snapshot(snapshot: RepoSnapshot, repo_root: Path) -> Path:
    """Write the snapshot to .idemra/brain/snapshot.json, overwriting any prior run."""
    target_dir = brain_dir(repo_root)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "snapshot.json"
    path.write_text(json.dumps(snapshot.to_dict(), indent=2, sort_keys=True) + "\n")
    return path
