"""Idemra CLI — Typer app, Rich output."""

from pathlib import Path

import typer
from rich.console import Console

from idemra.config.permissions import (
    InvalidPermissionsConfig,
    PermissionsNotFound,
    load_permissions,
)
from idemra.config.scaffold import idemra_dir, write_scaffold
from idemra.world_model.build import build_world_model
from idemra.world_model.snapshot import NotAGitRepo

app = typer.Typer(name="idemra", help="Production reliability infrastructure for coding agents.")
console = Console()


@app.command()
def init(repo: str = typer.Argument(".", help="Target repo to initialize Idemra into.")) -> None:
    """Create the .idemra/ folder structure and default config in a target repo."""
    repo_root = Path(repo).resolve()
    written = write_scaffold(repo_root)
    if not written:
        console.print(f"[yellow]{idemra_dir(repo_root)} already exists[/yellow] — leaving it untouched.")
        raise typer.Exit(code=0)

    console.print(f"[bold green]Initialized[/bold green] {idemra_dir(repo_root)}")
    for path in written:
        console.print(f"  created {path.relative_to(repo_root)}")


@app.command()
def config(repo: str = typer.Argument(".", help="Target repo to read Idemra config from.")) -> None:
    """Show current permission configuration."""
    repo_root = Path(repo).resolve()
    try:
        permissions = load_permissions(repo_root)
    except (PermissionsNotFound, InvalidPermissionsConfig) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    console.print("[bold]Layer 2 permissions[/bold] (permissions.yml)")
    console.print(f"  approval_required: {list(permissions.approval_required)}")
    console.print(f"  denied_paths: {list(permissions.denied_paths)}")


@app.command(name="index")
def index_cmd(repo: str = typer.Argument(".", help="Target repo to build the world model for.")) -> None:
    """Build the world model (structural snapshot + symbol index) for a target repo."""
    repo_root = Path(repo).resolve()
    try:
        result = build_world_model(repo_root)
    except NotAGitRepo as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[bold green]World model built[/bold green] for {repo_root}")
    console.print(f"  {len(result.snapshot.files)} files -> {result.snapshot_path.relative_to(repo_root)}")
    console.print(f"  {len(result.symbols)} symbols -> {result.symbols_path.relative_to(repo_root)}")


@app.command()
def run(repo: str, task: str) -> None:
    """Start a run: dispatch task against repo, blocking on the approval gate."""
    raise NotImplementedError("Phase 3: wires the Coder Agent to the orchestrator")


@app.command()
def status(run_id: str | None = None) -> None:
    """Show run status — all runs, or one by id."""
    raise NotImplementedError("Phase 2: query runs table")


@app.command()
def approve(run_id: str) -> None:
    """Approve a pending change, unblocking the apply step."""
    raise NotImplementedError("Phase 2: write approval decision event")


@app.command()
def reject(run_id: str, reason: str = "") -> None:
    """Reject a pending change."""
    raise NotImplementedError("Phase 2: write approval decision event")


@app.command()
def log(run_id: str) -> None:
    """Dump the full event history for a run."""
    raise NotImplementedError("Phase 2: event log dump")


@app.command(name="replay")
def replay_cmd(run_id: str) -> None:
    """Reconstruct run state deterministically from its event stream."""
    raise NotImplementedError("Phase 2: replay engine")


if __name__ == "__main__":
    app()
