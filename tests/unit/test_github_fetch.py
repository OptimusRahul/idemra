"""idemra.github.fetch — no real gh process anywhere in this suite.
subprocess.run is monkeypatched at the module boundary with a fake that
returns pre-programmed responses in call order, mirroring how the rest of
this suite avoids real LLM/network/Redis calls (see tests/unit/test_llm_router.py,
tests/conftest.py). A real `gh` call only ever happens in the manual
smoke test (docs/manual-testing.md).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from idemra.github.fetch import (
    MAX_FAILED_JOBS,
    MAX_LOG_LINES_PER_JOB,
    GitHubFetchError,
    _run_gh,
    task_from_check,
    task_from_issue,
)

REPO_ROOT = Path("/repo")
REPO_SLUG_RESPONSE = SimpleNamespace(returncode=0, stdout="owner/repo\n", stderr="")


class _FakeGh:
    """Returns queued responses in call order; records every invocation."""

    def __init__(self, *responses: SimpleNamespace) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[list[str], Path | None]] = []

    def __call__(self, cmd, capture_output, text, check, cwd=None):
        self.calls.append((cmd, cwd))
        return self._responses.pop(0)


def _ok(stdout: str) -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _fail(stderr: str) -> SimpleNamespace:
    return SimpleNamespace(returncode=1, stdout="", stderr=stderr)


# --- _run_gh -----------------------------------------------------------


def test_run_gh_returns_stdout_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGh(_ok("hello\n"))
    monkeypatch.setattr(subprocess, "run", fake)

    result = _run_gh("issue", "view", "1")

    assert result == "hello\n"
    assert fake.calls[0][0] == ["gh", "issue", "view", "1"]


def test_run_gh_raises_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _FakeGh(_fail("not found")))

    with pytest.raises(GitHubFetchError, match="not found"):
        _run_gh("issue", "view", "999")


def test_run_gh_raises_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(*args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(subprocess, "run", _missing)

    with pytest.raises(GitHubFetchError, match="gh CLI not found"):
        _run_gh("issue", "view", "1")


def test_run_gh_passes_cwd_through(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGh(_ok("owner/repo\n"))
    monkeypatch.setattr(subprocess, "run", fake)

    _run_gh("repo", "view", cwd=REPO_ROOT)

    assert fake.calls[0][1] == REPO_ROOT


# --- task_from_issue -----------------------------------------------------


def test_task_from_issue_builds_task_text_with_source_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    issue_json = json.dumps(
        {"title": "Fix the thing", "body": "It's broken.", "url": "https://github.com/owner/repo/issues/42"}
    )
    fake = _FakeGh(REPO_SLUG_RESPONSE, _ok(issue_json))
    monkeypatch.setattr(subprocess, "run", fake)

    result = task_from_issue("42", REPO_ROOT)

    assert result == "[source: https://github.com/owner/repo/issues/42]\n\nFix the thing\n\nIt's broken."
    assert fake.calls[1][0] == ["gh", "issue", "view", "42", "-R", "owner/repo", "--json", "title,body,url"]


def test_task_from_issue_handles_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    issue_json = json.dumps({"title": "Fix the thing", "body": "", "url": "https://github.com/owner/repo/issues/42"})
    monkeypatch.setattr(subprocess, "run", _FakeGh(REPO_SLUG_RESPONSE, _ok(issue_json)))

    result = task_from_issue("42", REPO_ROOT)

    assert "Fix the thing" in result
    assert result.endswith("\n\n")  # empty body still produces valid, non-crashing text


def test_task_from_issue_propagates_gh_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _FakeGh(REPO_SLUG_RESPONSE, _fail("issue not found")))

    with pytest.raises(GitHubFetchError, match="issue not found"):
        task_from_issue("999", REPO_ROOT)


# --- task_from_check -----------------------------------------------------


def _jobs_response(*jobs: dict) -> SimpleNamespace:
    return _ok(json.dumps({"jobs": list(jobs), "url": "https://github.com/owner/repo/actions/runs/123"}))


def _job(name: str, database_id: int, conclusion: str = "failure") -> dict:
    return {"name": name, "databaseId": database_id, "conclusion": conclusion}


def test_task_from_check_happy_path_short_log_not_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGh(
        REPO_SLUG_RESPONSE,
        _jobs_response(_job("test", 1)),
        _ok("line 1\nline 2\n"),
    )
    monkeypatch.setattr(subprocess, "run", fake)

    result = task_from_check("123", REPO_ROOT)

    assert "[source: https://github.com/owner/repo/actions/runs/123]" in result
    assert "## job: test" in result
    assert "line 1\nline 2" in result
    assert "truncated" not in result


def test_task_from_check_truncates_long_log_to_last_200_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    long_log = "\n".join(f"line {i}" for i in range(1, 251))
    fake = _FakeGh(
        REPO_SLUG_RESPONSE,
        _jobs_response(_job("test", 1)),
        _ok(long_log),
    )
    monkeypatch.setattr(subprocess, "run", fake)

    result = task_from_check("123", REPO_ROOT)

    assert "[... 50 lines truncated ...]" in result
    assert "line 51" in result  # first line of the kept tail
    assert "line 250" in result  # last line, always kept
    assert "line 1\n" not in result  # dropped from the head
    assert "line 50\n" not in result  # last dropped line
    kept_lines = [ln for ln in result.splitlines() if ln.startswith("line ")]
    assert len(kept_lines) == MAX_LOG_LINES_PER_JOB


def test_task_from_check_concatenates_multiple_failed_jobs_each_independently_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeGh(
        REPO_SLUG_RESPONSE,
        _jobs_response(_job("build", 1), _job("test", 2)),
        _ok("build failed here\n"),
        _ok("test failed here\n"),
    )
    monkeypatch.setattr(subprocess, "run", fake)

    result = task_from_check("123", REPO_ROOT)

    assert "## job: build" in result
    assert "build failed here" in result
    assert "## job: test" in result
    assert "test failed here" in result


def test_task_from_check_ignores_successful_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGh(
        REPO_SLUG_RESPONSE,
        _jobs_response(_job("lint", 1, conclusion="success"), _job("test", 2, conclusion="failure")),
        _ok("test failed here\n"),
    )
    monkeypatch.setattr(subprocess, "run", fake)

    result = task_from_check("123", REPO_ROOT)

    assert "## job: lint" not in result
    assert "## job: test" in result


def test_task_from_check_raises_when_run_has_no_failed_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGh(REPO_SLUG_RESPONSE, _jobs_response(_job("test", 1, conclusion="success")))
    monkeypatch.setattr(subprocess, "run", fake)

    with pytest.raises(GitHubFetchError, match="no failed jobs"):
        task_from_check("123", REPO_ROOT)


def test_task_from_check_caps_number_of_included_failed_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    many_jobs = [_job(f"job-{i}", i) for i in range(MAX_FAILED_JOBS + 5)]
    job_log_responses = [_ok(f"log for job {i}\n") for i in range(MAX_FAILED_JOBS)]
    fake = _FakeGh(REPO_SLUG_RESPONSE, _jobs_response(*many_jobs), *job_log_responses)
    monkeypatch.setattr(subprocess, "run", fake)

    result = task_from_check("123", REPO_ROOT)

    assert result.count("## job:") == MAX_FAILED_JOBS
    assert "[... 5 more failed job(s) omitted ...]" in result
