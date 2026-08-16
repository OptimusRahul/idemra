"""Read and validate the Layer 2 permissions.yml written by `idemra init`.

Parsing + validation only — nothing acts on this yet. Enforcement starts
in Phase 3 once agents exist to check actions/paths against it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from idemra.config.scaffold import idemra_dir

# The only action names Idemra currently knows about — matches the
# scaffold's default permissions.yml. Extend as new gated actions ship.
VALID_ACTIONS = frozenset({"write", "delete", "git_push"})


class PermissionsNotFound(Exception):
    pass


class InvalidPermissionsConfig(Exception):
    pass


@dataclass(frozen=True)
class PermissionsConfig:
    approval_required: tuple[str, ...]
    denied_paths: tuple[str, ...]


def _validate_approval_required(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise InvalidPermissionsConfig(f"approval_required must be a list, got {type(raw).__name__}")

    actions = []
    for action in raw:
        if not isinstance(action, str) or action not in VALID_ACTIONS:
            raise InvalidPermissionsConfig(
                f"unknown action {action!r} in approval_required — must be one of {sorted(VALID_ACTIONS)}"
            )
        actions.append(action)
    return tuple(actions)


def _validate_denied_paths(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise InvalidPermissionsConfig(f"denied_paths must be a list, got {type(raw).__name__}")

    patterns = []
    for pattern in raw:
        if not isinstance(pattern, str) or not pattern:
            raise InvalidPermissionsConfig(f"invalid glob pattern in denied_paths: {pattern!r}")
        if pattern.startswith("/") or ".." in Path(pattern).parts:
            raise InvalidPermissionsConfig(
                f"denied_paths entries must be repo-relative, no leading '/' or '..': {pattern!r}"
            )
        patterns.append(pattern)
    return tuple(patterns)


def parse_permissions(raw: dict) -> PermissionsConfig:
    return PermissionsConfig(
        approval_required=_validate_approval_required(raw.get("approval_required")),
        denied_paths=_validate_denied_paths(raw.get("denied_paths")),
    )


def load_permissions(repo_root: Path) -> PermissionsConfig:
    path = idemra_dir(repo_root) / "permissions.yml"
    if not path.exists():
        raise PermissionsNotFound(f"{path} does not exist — run `idemra init` first")
    raw = yaml.safe_load(path.read_text()) or {}
    return parse_permissions(raw)
