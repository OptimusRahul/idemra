"""Exercises .githooks/post-commit against real git/bash, with the Obsidian
vault and discord-relay paths swapped for temp dirs — no real vault, no
real Discord, no network."""

import shutil
import subprocess
from pathlib import Path

import pytest

HOOK_SOURCE = Path(__file__).parents[2] / ".githooks" / "post-commit"

STUB_POST_CLI = """\
#!/usr/bin/env node
const args = process.argv.slice(2);
const fs = require("fs");
fs.writeFileSync(process.env.RECEIPT_PATH, JSON.stringify(args));
"""


@pytest.fixture
def hooked_repo(tmp_path: Path):
    if shutil.which("node") is None:
        pytest.skip("node not available")

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)

    hooks_dir = repo / ".githooks"
    hooks_dir.mkdir()
    hook_dest = hooks_dir / "post-commit"
    hook_dest.write_text(HOOK_SOURCE.read_text())
    hook_dest.chmod(0o755)
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=repo, check=True)

    vault_dir = tmp_path / "vault" / "Idemra"
    relay_dir = tmp_path / "relay"
    (relay_dir / "scripts").mkdir(parents=True)
    (relay_dir / "scripts" / "post-cli.js").write_text(STUB_POST_CLI)
    receipt_path = tmp_path / "receipt.json"

    (repo / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)

    env = {
        "PATH": __import__("os").environ["PATH"],
        "HOME": str(tmp_path),
        "IDEMRA_HOOK_VAULT_DIR": str(vault_dir),
        "IDEMRA_HOOK_RELAY_DIR": str(relay_dir),
        "IDEMRA_HOOK_LOG": str(tmp_path / "hook.log"),
        "IDEMRA_HOOK_SYNC": "1",
        "RECEIPT_PATH": str(receipt_path),
    }

    return repo, vault_dir, receipt_path, env


def test_commit_appends_activity_log_and_posts_to_discord(hooked_repo):
    repo, vault_dir, receipt_path, env = hooked_repo

    subprocess.run(
        ["git", "commit", "-m", "Add README\n\nFixes #42, #7"],
        cwd=repo,
        check=True,
        env=env,
    )

    activity_log = vault_dir / "Idemra Activity Log.md"
    assert activity_log.exists()
    content = activity_log.read_text()
    assert "Add README" in content
    assert "Refs: #7 #42" in content
    assert "1 file(s) changed." in content

    receipt = receipt_path.read_text()
    assert "update" in receipt
    assert "agent-os" in receipt
    assert "Add README" in receipt
    assert "refs #7 #42" in receipt


def test_commit_without_issue_refs_omits_refs_line(hooked_repo):
    repo, vault_dir, _receipt_path, env = hooked_repo

    subprocess.run(["git", "commit", "-m", "No issue refs here"], cwd=repo, check=True, env=env)

    content = (vault_dir / "Idemra Activity Log.md").read_text()
    assert "No issue refs here" in content
    assert "Refs:" not in content


def test_second_commit_appends_not_overwrites(hooked_repo):
    repo, vault_dir, _receipt_path, env = hooked_repo

    subprocess.run(["git", "commit", "-m", "First"], cwd=repo, check=True, env=env)
    (repo / "second.txt").write_text("x\n")
    subprocess.run(["git", "add", "second.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "Second"], cwd=repo, check=True, env=env)

    content = (vault_dir / "Idemra Activity Log.md").read_text()
    assert "First" in content
    assert "Second" in content
    assert content.count("# Idemra — Activity Log") == 1
