"""Coder Agent — propose_changes() parses the model's JSON proposal into
ProposedFile objects. No real LLM call: complete is always a fake callable.
"""

import json

import pytest

from idemra.agents.coder import MalformedProposal, propose_changes
from idemra.world_model.build import WorldModelResult
from idemra.world_model.snapshot import FileEntry, GitMetadata, RepoSnapshot
from idemra.world_model.symbols import Symbol


@pytest.fixture
def world_model() -> WorldModelResult:
    snapshot = RepoSnapshot(
        repo_root="/tmp/repo",
        git=GitMetadata(branch="main", commit="a" * 40, dirty=False),
        files=[FileEntry(path="src/main.py", size_bytes=10, language="python")],
    )
    symbols = [Symbol(name="main", kind="function", path="src/main.py", line=1)]
    return WorldModelResult(snapshot=snapshot, symbols=symbols, snapshot_path=None, symbols_path=None)


def test_propose_changes_parses_valid_json(world_model: WorldModelResult) -> None:
    payload = json.dumps([{"path": "src/main.py", "content": "print('hi')\n"}])

    result = propose_changes("fix the bug", world_model, complete=lambda prompt: payload)

    assert len(result) == 1
    assert result[0].path == "src/main.py"
    assert result[0].content == "print('hi')\n"


def test_propose_changes_strips_markdown_fences(world_model: WorldModelResult) -> None:
    payload = json.dumps([{"path": "src/main.py", "content": "x = 1\n"}])
    fenced = f"```json\n{payload}\n```"

    result = propose_changes("fix the bug", world_model, complete=lambda prompt: fenced)

    assert result[0].content == "x = 1\n"


def test_propose_changes_raises_on_invalid_json(world_model: WorldModelResult) -> None:
    with pytest.raises(MalformedProposal):
        propose_changes("fix the bug", world_model, complete=lambda prompt: "not json at all")


def test_propose_changes_raises_on_non_array_json(world_model: WorldModelResult) -> None:
    with pytest.raises(MalformedProposal):
        propose_changes("fix the bug", world_model, complete=lambda prompt: json.dumps({"path": "x"}))


def test_propose_changes_raises_on_malformed_entry(world_model: WorldModelResult) -> None:
    payload = json.dumps([{"path": "src/main.py"}])  # missing "content"

    with pytest.raises(MalformedProposal):
        propose_changes("fix the bug", world_model, complete=lambda prompt: payload)


def test_propose_changes_includes_task_and_files_in_prompt(world_model: WorldModelResult) -> None:
    captured = {}

    def fake_complete(prompt: str) -> str:
        captured["prompt"] = prompt
        return json.dumps([])

    propose_changes("implement feature X", world_model, complete=fake_complete)

    assert "implement feature X" in captured["prompt"]
    assert "src/main.py" in captured["prompt"]
    assert "main" in captured["prompt"]
