"""Idemra CLI — Typer app, Rich output."""

from datetime import timedelta
from pathlib import Path

import typer
from rich.console import Console

from idemra.agents.coder import MalformedProposal, propose_changes
from idemra.config.permissions import (
    InvalidPermissionsConfig,
    PermissionsNotFound,
    load_permissions,
)
from idemra.config.scaffold import idemra_dir, write_scaffold
from idemra.db.session import session_scope
from idemra.llm.router import complete as llm_complete
from idemra.orchestrator.master import record_routing_decision, route_task
from idemra.orchestrator.runs import (
    ApprovalConflict,
    NoPendingApproval,
    RunNotFound,
    apply_run,
    create_run,
    fail_run,
    get_events,
    get_run,
    list_runs,
    record_approval_decision,
    record_proposal,
    replay_run,
    request_approval,
    start_run,
    validate_proposal,
)
from idemra.permissions.layer2 import requires_approval
from idemra.world_model.build import build_world_model
from idemra.world_model.snapshot import NotAGitRepo

APPROVAL_TTL = timedelta(hours=24)

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
    """Start a run: route the task, propose a change via the Coder Agent,
    then gate it on approval.

    Does not block — creates the proposal and (if required) the approval
    request, then exits. Use `idemra approve`/`idemra reject` to continue.

    Every failure mode below now happens inside a Run row that already
    exists — including routing rejection, missing permissions.yml, and a
    non-git repo — so `idemra status`/`idemra log` can show it. Phase 3
    checked permissions/world-model *before* creating the run, which left
    those two failure classes with no audit trail at all.
    """
    repo_root = Path(repo).resolve()

    with session_scope() as session:
        run_row = create_run(session, repo=str(repo_root), task=task)

        decision = route_task(task, repo_root)
        record_routing_decision(session, run_row, decision)
        if not decision.accepted:
            fail_run(session, run_row, f"rejected by router: {decision.reason}")
            console.print(f"[red]Run {run_row.id} rejected[/red] by router: {decision.reason}")
            raise typer.Exit(code=1)

        start_run(session, run_row)

        try:
            permissions = load_permissions(repo_root)
        except (PermissionsNotFound, InvalidPermissionsConfig) as exc:
            fail_run(session, run_row, str(exc))
            console.print(f"[red]Run {run_row.id} failed[/red]: {exc}")
            raise typer.Exit(code=1)

        try:
            world_model = build_world_model(repo_root)
        except NotAGitRepo as exc:
            fail_run(session, run_row, str(exc))
            console.print(f"[red]Run {run_row.id} failed[/red]: {exc}")
            raise typer.Exit(code=1)

        try:
            proposed = propose_changes(task, world_model, complete=llm_complete)
        except MalformedProposal as exc:
            fail_run(session, run_row, f"malformed proposal: {exc}")
            console.print(f"[red]Run {run_row.id} failed[/red] — Coder Agent returned a malformed proposal")
            raise typer.Exit(code=1)

        record_proposal(session, run_row, proposed)

        violations = validate_proposal(proposed, repo_root, permissions)
        if violations:
            fail_run(session, run_row, "; ".join(violations))
            console.print(f"[red]Run {run_row.id} failed[/red] permission checks:")
            for v in violations:
                console.print(f"  {v}")
            raise typer.Exit(code=1)

        if requires_approval("write", permissions):
            request_approval(session, run_row, step_id="apply", ttl=APPROVAL_TTL)
            console.print(
                f"[bold]Run {run_row.id}[/bold] created, awaiting approval "
                f"({len(proposed)} file(s) proposed)"
            )
            console.print(f"  see: idemra approve {run_row.id}  /  idemra reject {run_row.id}")
        else:
            apply_run(session, run_row, repo_root, permissions)
            console.print(
                f"[bold green]Run {run_row.id} completed[/bold green] — "
                f"{len(proposed)} file(s) written (approval not required)"
            )


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
    """Approve a pending change, then apply it — unless it's already been
    applied (idempotent retry), in which case this is a no-op."""
    with session_scope() as session:
        try:
            record_approval_decision(session, run_id, "approved")
        except (RunNotFound, NoPendingApproval, ApprovalConflict) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        run_row = get_run(session, run_id)
        if run_row.status != "approved":
            console.print(
                f"[bold green]Approved[/bold green] run {run_id} "
                f"(already {run_row.status} — nothing more to apply)"
            )
            return

        console.print(f"[bold green]Approved[/bold green] run {run_id}")

        try:
            permissions = load_permissions(Path(run_row.repo))
        except (PermissionsNotFound, InvalidPermissionsConfig) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        apply_run(session, run_row, Path(run_row.repo), permissions)

        if run_row.status == "completed":
            console.print(f"[bold green]Run {run_id} completed[/bold green]")
        else:
            console.print(f"[red]Run {run_id} failed to apply[/red] — see: idemra log {run_id}")
            raise typer.Exit(code=1)


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
