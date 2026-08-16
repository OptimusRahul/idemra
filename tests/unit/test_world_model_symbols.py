"""Symbol index — tree-sitter over Python source, driven off the snapshot.

Covers the "where is X defined" done-when bar for Phase 2: functions,
classes (including methods nested in a class body), and imports (plain,
aliased, multi-name, and wildcard) must all resolve to a symbol entry.
"""

import json
import subprocess
from pathlib import Path

import pytest

from idemra.world_model.snapshot import build_snapshot
from idemra.world_model.symbols import build_symbol_index, write_symbol_index


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
    (repo / "src" / "main.py").write_text(
        "import os\n"
        "from pathlib import Path, PurePath\n"
        "import os.path as osp\n"
        "from typing import *\n"
        "\n"
        "class Foo:\n"
        "    def bar(self):\n"
        "        pass\n"
        "\n"
        "def baz():\n"
        "    pass\n"
    )
    (repo / "README.md").write_text("# repo\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def test_build_symbol_index_finds_functions_and_classes(git_repo: Path) -> None:
    symbols = build_symbol_index(git_repo)

    by_name = {s.name: s for s in symbols if s.kind in ("function", "class")}
    assert by_name["Foo"].kind == "class"
    assert by_name["Foo"].line == 6
    assert by_name["bar"].kind == "function"  # method, nested in the class body
    assert by_name["baz"].kind == "function"
    assert by_name["baz"].line == 10


def test_build_symbol_index_finds_imports(git_repo: Path) -> None:
    symbols = build_symbol_index(git_repo)

    imports = {s.name for s in symbols if s.kind == "import"}
    assert imports == {"os", "pathlib", "Path", "PurePath", "os.path", "typing"}


def test_build_symbol_index_skips_non_python_files(git_repo: Path) -> None:
    symbols = build_symbol_index(git_repo)

    assert all(s.path.endswith(".py") for s in symbols)


def test_build_symbol_index_reuses_a_passed_in_snapshot(git_repo: Path) -> None:
    snapshot = build_snapshot(git_repo)

    symbols = build_symbol_index(git_repo, snapshot=snapshot)

    assert any(s.name == "baz" for s in symbols)


def test_write_symbol_index_produces_valid_json_in_brain_dir(git_repo: Path) -> None:
    symbols = build_symbol_index(git_repo)

    path = write_symbol_index(symbols, git_repo)

    assert path == git_repo / ".idemra" / "brain" / "symbols.json"
    data = json.loads(path.read_text())
    assert {"baz", "Foo", "bar"}.issubset({entry["name"] for entry in data})
