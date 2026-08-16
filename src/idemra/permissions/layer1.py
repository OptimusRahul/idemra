"""Layer 1 — hardcoded, non-negotiable safety rules.

Not configurable, not overridable by permissions.yml (that's Layer 2).
These exist before agents do, because Month 3's plan is running this
against real open-source repos this project doesn't own.
"""

from pathlib import Path

DENIED_PATH_PATTERNS = (".env", "*.pem", "*.key", ".ssh/*", "id_rsa*")
DENIED_GIT_OPS = ("push --force", "reset --hard", "clean -f", "checkout --")


class PermissionViolation(Exception):
    pass


def assert_within_repo_root(target: Path, repo_root: Path) -> None:
    resolved = target.resolve()
    if repo_root.resolve() not in resolved.parents and resolved != repo_root.resolve():
        raise PermissionViolation(f"{target} escapes repo root {repo_root}")


def assert_not_denied_path(target: Path) -> None:
    for pattern in DENIED_PATH_PATTERNS:
        if target.match(pattern):
            raise PermissionViolation(f"{target} matches denied pattern {pattern}")


def assert_not_denied_git_op(command: str) -> None:
    for denied in DENIED_GIT_OPS:
        if denied in command:
            raise PermissionViolation(f"git command '{command}' contains denied op '{denied}'")
