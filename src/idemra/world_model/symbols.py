"""Symbol index — tree-sitter-based, Python source files only for Phase 2.

Extends the structural snapshot with a queryable symbol table
(functions/classes/imports) so an agent (Phase 3) can answer "where is X
defined" instead of grepping the whole tree. Other languages come later —
Phase 2's done-when bar is Python only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from idemra.world_model.snapshot import RepoSnapshot, brain_dir, build_snapshot

PY_LANGUAGE = Language(tspython.language())

_DEFINITION_NODE_KINDS = {
    "function_definition": "function",
    "class_definition": "class",
}
_IMPORT_NODE_TYPES = ("import_statement", "import_from_statement")


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str  # "function" | "class" | "import"
    path: str  # posix, relative to repo_root
    line: int  # 1-indexed


def _identifier_name(node: Node) -> str | None:
    for child in node.children:
        if child.type == "identifier" and child.text:
            return child.text.decode("utf-8")
    return None


def _dotted_names(node: Node) -> list[str]:
    names = []
    for child in node.children:
        if child.type == "dotted_name" and child.text:
            names.append(child.text.decode("utf-8"))
        elif child.type == "aliased_import":
            names.extend(_dotted_names(child))
    return names


def _extract_symbols(tree_root: Node, relative_path: str) -> list[Symbol]:
    symbols: list[Symbol] = []

    def visit(node: Node) -> None:
        kind = _DEFINITION_NODE_KINDS.get(node.type)
        if kind:
            name = _identifier_name(node)
            if name:
                symbols.append(Symbol(name=name, kind=kind, path=relative_path, line=node.start_point[0] + 1))
        elif node.type in _IMPORT_NODE_TYPES:
            for name in _dotted_names(node):
                symbols.append(Symbol(name=name, kind="import", path=relative_path, line=node.start_point[0] + 1))

        for child in node.children:
            visit(child)

    visit(tree_root)
    return symbols


def build_symbol_index(repo_root: Path, snapshot: RepoSnapshot | None = None) -> list[Symbol]:
    """Parse every Python file in the snapshot into a queryable symbol table."""
    repo_root = repo_root.resolve()
    snapshot = snapshot or build_snapshot(repo_root)
    parser = Parser(PY_LANGUAGE)

    symbols: list[Symbol] = []
    for entry in snapshot.files:
        if entry.language != "python":
            continue
        source = (repo_root / entry.path).read_bytes()
        tree = parser.parse(source)
        symbols.extend(_extract_symbols(tree.root_node, entry.path))

    return symbols


def write_symbol_index(symbols: list[Symbol], repo_root: Path) -> Path:
    """Write the symbol index to .idemra/brain/symbols.json, overwriting any prior run."""
    target_dir = brain_dir(repo_root)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "symbols.json"
    payload = [{"name": s.name, "kind": s.kind, "path": s.path, "line": s.line} for s in symbols]
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path
