"""Layer 2 — user-configurable permissions, layered on top of Layer 1.

Parsing lives in config/permissions.py (Phase 2); this is where the parsed
config becomes an actual gate (Phase 3, once agents exist to act on it).
Raises the same PermissionViolation Layer 1 uses — callers don't need to
know which layer denied a path, only that it was denied.
"""

from __future__ import annotations

from pathlib import Path

from idemra.config.permissions import PermissionsConfig
from idemra.permissions.layer1 import PermissionViolation


def assert_not_layer2_denied(target: Path, config: PermissionsConfig) -> None:
    for pattern in config.denied_paths:
        if target.match(pattern):
            raise PermissionViolation(f"{target} matches Layer 2 denied pattern {pattern}")


def requires_approval(action: str, config: PermissionsConfig) -> bool:
    return action in config.approval_required
