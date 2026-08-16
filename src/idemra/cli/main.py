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
from idemra.db.session import session_scope
from idemra.orchestrator.runs import (
    ApprovalConflict,
    NoPendingApproval,
    RunNotFound,
    get_events,
    get_run,
    list_runs,
    record_approval_decision,
    replay_run,
)
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
def status(run_id: str | None = typer.Argument(None)) -> None:
    """Show run status — all runs, or one by id."""
    with session_scope() as session:
        if run_id is None:
            runs = list_runs(session)
            if not runs:
                console.print("[yellow]No runs yet.[/yellow]")
                return
            for r in runs:
                console.print(f"  {r.id}  {r.status:<18} {r.task}")
            return

        try:
            r = get_run(session, run_id)
        except RunNotFound as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        console.print(f"[bold]{r.id}[/bold]  status={r.status}  task={r.task!r}  repo={r.repo}")


@app.command()
def approve(run_id: str) -> None:
    """Approve a pending change, unblocking the apply step."""
    with session_scope() as session:
        try:
            record_approval_decision(session, run_id, "approved")
        except (RunNotFound, NoPendingApproval, ApprovalConflict) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
    console.print(f"[bold green]Approved[/bold green] run {run_id}")


@app.command()
def reject(run_id: str, reason: str = "") -> None:
    """Reject a pending change."""
    with session_scope() as session:
        try:
            record_approval_decision(session, run_id, "rejected", reason=reason or None)
        except (RunNotFound, NoPendingApproval, ApprovalConflict) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
    console.print(f"[bold red]Rejected[/bold red] run {run_id}")


@app.command()
def log(run_id: str) -> None:
    """Dump the full event history for a run."""
    with session_scope() as session:
        try:
            events = get_events(session, run_id)
        except RunNotFound as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        if not events:
            console.print("[yellow]No events for this run yet.[/yellow]")
            return
        for e in events:
            console.print(f"  seq={e.seq}  {e.type}  {e.payload}")


@app.command(name="replay")
def replay_cmd(run_id: str) -> None:
    """Reconstruct run state deterministically from its event stream."""
    with session_scope() as session:
        try:
            result = replay_run(session, run_id)
        except RunNotFound as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
    console.print(f"[bold]{result.run_id}[/bold]  reconstructed status={result.status}")
    console.print(f"  applied {len(result.applied_event_types)} event(s): {result.applied_event_types}")


if __name__ == "__main__":
    app()
