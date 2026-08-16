from pathlib import Path

import pytest

from idemra.config.permissions import (
    InvalidPermissionsConfig,
    PermissionsNotFound,
    load_permissions,
    parse_permissions,
)
from idemra.config.scaffold import idemra_dir, write_scaffold


def test_load_permissions_reads_defaults(tmp_path: Path) -> None:
    write_scaffold(tmp_path)

    permissions = load_permissions(tmp_path)

    assert permissions.approval_required == ("write", "delete", "git_push")
    assert permissions.denied_paths == ()


def test_load_permissions_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(PermissionsNotFound):
        load_permissions(tmp_path)


def test_parse_permissions_accepts_empty_config() -> None:
    permissions = parse_permissions({})

    assert permissions.approval_required == ()
    assert permissions.denied_paths == ()


def test_parse_permissions_accepts_valid_denied_path_globs() -> None:
    permissions = parse_permissions({"denied_paths": ["secrets/*.json", "*.pem"]})

    assert permissions.denied_paths == ("secrets/*.json", "*.pem")


@pytest.mark.parametrize(
    "raw",
    [
        {"approval_required": ["write", "launch_missiles"]},
        {"approval_required": "write"},  # not a list
        {"approval_required": [123]},
    ],
)
def test_parse_permissions_rejects_unknown_or_malformed_actions(raw: dict) -> None:
    with pytest.raises(InvalidPermissionsConfig):
        parse_permissions(raw)


@pytest.mark.parametrize(
    "raw",
    [
        {"denied_paths": ["/etc/passwd"]},  # absolute, escapes repo-relative scoping
        {"denied_paths": ["../outside"]},  # traversal
        {"denied_paths": [""]},  # empty pattern
        {"denied_paths": "*.pem"},  # not a list
    ],
)
def test_parse_permissions_rejects_malformed_denied_paths(raw: dict) -> None:
    with pytest.raises(InvalidPermissionsConfig):
        parse_permissions(raw)


def test_load_permissions_fails_loudly_on_malformed_config(tmp_path: Path) -> None:
    write_scaffold(tmp_path)
    (idemra_dir(tmp_path) / "permissions.yml").write_text("approval_required: [not_a_real_action]\n")

    with pytest.raises(InvalidPermissionsConfig):
        load_permissions(tmp_path)
