"""Read the Layer 2 permissions.yml written by `idemra init`."""

from pathlib import Path

import yaml

from idemra.config.scaffold import idemra_dir


class PermissionsNotFound(Exception):
    pass


def load_permissions(repo_root: Path) -> dict:
    path = idemra_dir(repo_root) / "permissions.yml"
    if not path.exists():
        raise PermissionsNotFound(f"{path} does not exist — run `idemra init` first")
    return yaml.safe_load(path.read_text()) or {}
