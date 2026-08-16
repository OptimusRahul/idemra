"""Orchestrates a full world-model build: snapshot + symbol index.

Both artifacts are built from the same snapshot and written to
.idemra/brain/ in one pass, so a caller (the CLI, later) has a single
entry point instead of having to sequence snapshot.py and symbols.py
correctly by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from idemra.world_model.snapshot import RepoSnapshot, build_snapshot, write_snapshot
from idemra.world_model.symbols import Symbol, build_symbol_index, write_symbol_index


@dataclass(frozen=True)
class WorldModelResult:
    snapshot: RepoSnapshot
    symbols: list[Symbol]
    snapshot_path: Path
    symbols_path: Path


def build_world_model(repo_root: Path) -> WorldModelResult:
    """Build and write the full world model (snapshot + symbol index).

    Idempotent: each artifact is fully regenerated and overwritten on every
    call, so re-running never leaves stale entries from deleted/renamed files.
    """
    repo_root = repo_root.resolve()
    snapshot = build_snapshot(repo_root)
    symbols = build_symbol_index(repo_root, snapshot=snapshot)

    snapshot_path = write_snapshot(snapshot, repo_root)
    symbols_path = write_symbol_index(symbols, repo_root)

    return WorldModelResult(
        snapshot=snapshot,
        symbols=symbols,
        snapshot_path=snapshot_path,
        symbols_path=symbols_path,
    )
