"""Task text sourced from GitHub, via the `gh` CLI — the two functions
`idemra run --from-issue`/`--from-check` build a task string from.

`gh` CLI over a Python client (PyGithub/ghapi): zero new dependency,
reuses the developer's existing `gh auth login` session instead of adding
token management, matches ADR 0004's boring-tooling bias. See
docs/designs/phase-6-github-self-healing.md for the full trade-off.

Both `gh issue view` and `gh run view` are resolved against the *target*
repo (`repo_root`, the repo `idemra run` was pointed at), not whatever
repo idemra's own working directory happens to be in — `gh repo view` run
with `cwd=repo_root` once resolves the `owner/repo` slug, then every
subsequent call passes `-R <slug>` explicitly. `gh issue view` accepts a
full URL directly, but `gh run view` does not (confirmed by hand against
this project's own GitHub Actions runs) — bare issue numbers/run IDs plus
an explicit `-R` is the one interface that works consistently for both,
so that's what `task_from_issue`/`task_from_check` both take.

`_run_gh` is the single subprocess-wrapping site both public functions
call — same precedent as `record_event()` being the single Event(...)
construction site in orchestrator/runs.py.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

MAX_LOG_LINES_PER_JOB = 200
MAX_FAILED_JOBS = 10


class GitHubFetchError(Exception):
    pass


def _run_gh(*args: str, cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False, cwd=cwd)
    except FileNotFoundError as exc:
        raise GitHubFetchError("gh CLI not found on PATH — install it and run `gh auth login`") from exc

    if result.returncode != 0:
        raise GitHubFetchError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _repo_slug(repo_root: Path) -> str:
    return _run_gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner", cwd=repo_root).strip()


def _truncate(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    dropped = len(lines) - max_lines
    return f"[... {dropped} lines truncated ...]\n" + "\n".join(lines[-max_lines:])


def task_from_issue(issue_ref: str, repo_root: Path) -> str:
    """issue_ref: a bare issue number, e.g. "42"."""
    repo = _repo_slug(repo_root)
    raw = _run_gh("issue", "view", issue_ref, "-R", repo, "--json", "title,body,url")
    data = json.loads(raw)
    return f"[source: {data['url']}]\n\n{data['title']}\n\n{data['body']}"


def task_from_check(run_ref: str, repo_root: Path) -> str:
    """run_ref: a bare GitHub Actions run ID, e.g. "32176517316".

    Raises GitHubFetchError if the run has no failed jobs — a common case
    (someone re-running --from-check against a run that got fixed by
    something else) that deserves a specific message, not a generic
    "task cannot be empty" from route_task() further down the pipeline.
    """
    repo = _repo_slug(repo_root)
    raw = _run_gh("run", "view", run_ref, "-R", repo, "--json", "jobs,url")
    data = json.loads(raw)
    failed_jobs = [j for j in data["jobs"] if j["conclusion"] == "failure"]
    if not failed_jobs:
        raise GitHubFetchError(f"run {run_ref} has no failed jobs")

    included_jobs = failed_jobs[:MAX_FAILED_JOBS]
    omitted_count = len(failed_jobs) - len(included_jobs)

    sections = []
    for job in included_jobs:
        job_log = _run_gh("run", "view", run_ref, "-R", repo, "--job", str(job["databaseId"]), "--log-failed")
        sections.append(f"## job: {job['name']}\n{_truncate(job_log, MAX_LOG_LINES_PER_JOB)}")

    body = "\n\n".join(sections)
    if omitted_count:
        body += f"\n\n[... {omitted_count} more failed job(s) omitted ...]"

    return f"[source: {data['url']}]\n\n{body}"
