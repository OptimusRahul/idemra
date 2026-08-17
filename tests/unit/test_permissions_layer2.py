"""Layer 2 enforcement — the point where Phase 2's parsed-but-inert
permissions.yml becomes load-bearing. Same test style as Layer 1's suite."""

from pathlib import Path

import pytest

from idemra.config.permissions import PermissionsConfig
from idemra.permissions.layer1 import PermissionViolation
from idemra.permissions.layer2 import assert_not_layer2_denied, requires_approval

# --- assert_not_layer2_denied ---


@pytest.mark.parametrize(
    "pattern,path",
    [
        ("secrets/*.json", "secrets/creds.json"),
        ("*.pem", "server.pem"),
        ("config/*.yml", "config/prod.yml"),
    ],
)
def test_denied_paths_are_blocked(pattern: str, path: str) -> None:
    config = PermissionsConfig(approval_required=(), denied_paths=(pattern,))

    with pytest.raises(PermissionViolation):
        assert_not_layer2_denied(Path(path), config)


@pytest.mark.parametrize(
    "path",
    ["src/main.py", "README.md", "config/dev.json"],
)
def test_ordinary_paths_are_not_denied(path: str) -> None:
    config = PermissionsConfig(approval_required=(), denied_paths=("secrets/*.json", "*.pem"))

    assert_not_layer2_denied(Path(path), config)  # must not raise


def test_empty_denied_paths_denies_nothing() -> None:
    config = PermissionsConfig(approval_required=(), denied_paths=())

    assert_not_layer2_denied(Path("anything.py"), config)  # must not raise


# --- requires_approval ---


def test_requires_approval_true_when_action_configured() -> None:
    config = PermissionsConfig(approval_required=("write", "delete"), denied_paths=())

    assert requires_approval("write", config) is True


def test_requires_approval_false_when_action_not_configured() -> None:
    config = PermissionsConfig(approval_required=("delete",), denied_paths=())

    assert requires_approval("write", config) is False


def test_requires_approval_false_with_empty_config() -> None:
    config = PermissionsConfig(approval_required=(), denied_paths=())

    assert requires_approval("write", config) is False
