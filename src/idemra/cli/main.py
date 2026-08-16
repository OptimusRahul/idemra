"""Idemra CLI — Typer app, Rich output."""

import typer
from rich.console import Console

app = typer.Typer(name="idemra", help="Production reliability infrastructure for coding agents.")
console = Console()


@app.command()
def init(repo: str = typer.Argument(".", help="Target repo to initialize Idemra into.")) -> None:
    """Create the .idemra/ folder structure and default config in a target repo."""
    console.print(f"[bold]idemra init[/bold] — scaffolding .idemra/ in {repo}")
    raise NotImplementedError("Phase 1: create .idemra/ tree + permissions.yml/outcomes.yml")


@app.command()
def config() -> None:
    """Show current permission configuration."""
    raise NotImplementedError("Phase 1: read + render permissions.yml")


@app.command()
def run(repo: str, task: str) -> None:
    """Start a run: dispatch task against repo, blocking on the approval gate."""
    raise NotImplementedError("Phase 3: wires the Coder Agent to the orchestrator")


@app.command()
def status(run_id: str | None = None) -> None:
    """Show run status — all runs, or one by id."""
    raise NotImplementedError("Phase 1: query runs table")


@app.command()
def approve(run_id: str) -> None:
    """Approve a pending change, unblocking the apply step."""
    raise NotImplementedError("Phase 1: write approval decision event")


@app.command()
def reject(run_id: str, reason: str = "") -> None:
    """Reject a pending change."""
    raise NotImplementedError("Phase 1: write approval decision event")


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
