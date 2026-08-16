"""Default .idemra/ config scaffolding — written by `idemra init` into a target repo."""

from pathlib import Path

IDEMRA_DIR_NAME = ".idemra"

PERMISSIONS_YML = """\
# Layer 2 — user-configurable permissions, layered on top of Idemra's hardcoded
# Layer 1 rules (repo-root jail, secret-file denylist, destructive-git-op denylist).
# Layer 1 is never overridable from here.

approval_required:
  - write
  - delete
  - git_push

# Additional path patterns to deny, beyond Layer 1's hardcoded denylist.
denied_paths: []
"""

OUTCOMES_YML = """\
# What "success" means for a run. Read by later phases (self-healing, eval).
# Unused in Phase 1.
success_criteria: []
"""

MCP_YML = """\
# MCP servers Idemra may call on behalf of an agent. Wired starting Phase 7.5.
servers: []
"""

WEB_SEARCH_YML = """\
# Web search provider config for agents that need it. Wired starting Phase 7.5.
enabled: false
provider: null
"""

IDEMRA_GITIGNORE = """\
# Generated at runtime — may be large, and brain/receipts/ risk holding
# pre-redaction content. Only the .yml config files above are committable.
brain/
memory/
queue/
logs/
receipts/
"""

SCAFFOLD_FILES = {
    "permissions.yml": PERMISSIONS_YML,
    "outcomes.yml": OUTCOMES_YML,
    "mcp.yml": MCP_YML,
    "web_search.yml": WEB_SEARCH_YML,
    ".gitignore": IDEMRA_GITIGNORE,
}


def idemra_dir(repo_root: Path) -> Path:
    return repo_root / IDEMRA_DIR_NAME


def write_scaffold(repo_root: Path) -> list[Path]:
    """Create .idemra/ and its default config files.

    No-op (returns []) if .idemra/ already exists — init never overwrites
    a repo's existing config.
    """
    target_dir = idemra_dir(repo_root)
    if target_dir.exists():
        return []

    target_dir.mkdir(parents=True)
    written = []
    for name, content in SCAFFOLD_FILES.items():
        path = target_dir / name
        path.write_text(content)
        written.append(path)
    return written
