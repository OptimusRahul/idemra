"""Layer 1 is the hardcoded, non-configurable safety net — repo-root jail,
secret-file denylist, destructive-git-op denylist. It runs against real
external repos starting Month 3, so every rule here needs a test proving
it actually blocks what it claims to."""

from pathlib import Path

import pytest

from idemra.permissions.layer1 import (
    PermissionViolation,
    assert_not_denied_git_op,
    assert_not_denied_path,
    assert_within_repo_root,
)

# --- assert_within_repo_root ---


def test_path_inside_repo_root_is_allowed(tmp_path: Path) -> None:
    assert_within_repo_root(tmp_path / "file.txt", tmp_path)


def test_path_equal_to_repo_root_is_allowed(tmp_path: Path) -> None:
    assert_within_repo_root(tmp_path, tmp_path)


def test_nested_path_inside_repo_root_is_allowed(tmp_path: Path) -> None:
    assert_within_repo_root(tmp_path / "a" / "b" / "file.txt", tmp_path)


def test_sibling_path_outside_repo_root_is_blocked(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    with pytest.raises(PermissionViolation):
        assert_within_repo_root(outside, tmp_path)


def test_dotdot_traversal_out_of_repo_root_is_blocked(tmp_path: Path) -> None:
    escaping = tmp_path / ".." / "outside.txt"
    with pytest.raises(PermissionViolation):
        assert_within_repo_root(escaping, tmp_path)


# --- assert_not_denied_path ---


@pytest.mark.parametrize(
    "path",
    [
        "/repo/.env",
        "/repo/sub/.env",
        "/repo/server.pem",
        "/repo/sub/server.pem",
        "/repo/api.key",
        "/repo/.ssh/id_rsa",
        "/repo/sub/.ssh/id_rsa",
        "/repo/id_rsa",
        "/repo/id_rsa.pub",
    ],
)
def test_denied_secret_paths_are_blocked(path: str) -> None:
    with pytest.raises(PermissionViolation):
        assert_not_denied_path(Path(path))


@pytest.mark.parametrize(
    "path",
    [
        "/repo/src/main.py",
        "/repo/README.md",
        "/repo/env.py",  # not literally ".env"
        "/repo/permissions.yml",
    ],
)
def test_ordinary_paths_are_not_denied(path: str) -> None:
    assert_not_denied_path(Path(path))  # must not raise


# --- assert_not_denied_git_op ---


@pytest.mark.parametrize(
    "command",
    [
        "git push --force origin main",
        "git reset --hard HEAD~1",
        "git clean -f -d",
        "git checkout -- .",
    ],
)
def test_denied_git_ops_are_blocked(command: str) -> None:
    with pytest.raises(PermissionViolation):
        assert_not_denied_git_op(command)


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git reset --soft HEAD~1",
        "git status",
        "git checkout feature-branch",
    ],
)
def test_ordinary_git_ops_are_not_denied(command: str) -> None:
    assert_not_denied_git_op(command)  # must not raise
