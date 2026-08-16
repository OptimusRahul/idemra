from pathlib import Path

import pytest

from idemra.config.permissions import PermissionsNotFound, load_permissions
from idemra.config.scaffold import write_scaffold


def test_load_permissions_reads_defaults(tmp_path: Path) -> None:
    write_scaffold(tmp_path)

    permissions = load_permissions(tmp_path)

    assert permissions["approval_required"] == ["write", "delete", "git_push"]
    assert permissions["denied_paths"] == []


def test_load_permissions_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(PermissionsNotFound):
        load_permissions(tmp_path)
