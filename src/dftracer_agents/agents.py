"""
Install the bundled Claude Code subagent definitions into a discoverable
location.

Subagents are bundled inside the package at
``dftracer_agents/.agents/agents/<name>.md``. Claude Code discovers project
subagents under ``<root>/.claude/agents/<name>.md`` — this module symlinks the
bundled agent files there (never copies, so upgrading the package or editing a
bundled agent is immediately reflected).

Each bundled agent scopes a single dftracer pipeline stage to a specific
model + a specific MCP-tool allowlist, so the stage runs in a small, cheap,
cold context. A `dftracer-pipeline-planner` agent (larger model) plans the run
and the main thread hands each stage to the matching executor subagent.

Mirrors ``dftracer_agents.skills`` (which installs skill directories into
``.claude/skills/``); this module installs agent files into ``.claude/agents/``
and shares its state-tracking + target-resolution helpers.

CLI usage (after pip install)::

    dftracer-install-agents                  # interactive: asks where
    dftracer-install-agents --target cwd     # ./.claude/agents/
    dftracer-install-agents --target global  # ~/.claude/agents/
    dftracer-install-agents --list           # print bundled agent names

Programmatic usage::

    from dftracer_agents.agents import ensure_agents_setup
    ensure_agents_setup()                     # idempotent; used by mcp_server startup
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Reuse the exact state-file + target-resolution logic the skills installer
# uses, so agents and skills behave identically (same ~/.dftracer-agents state,
# same "CWD-if-project-else-home" default, same self-heal semantics).
from dftracer_agents.skills import (
    _load_state,
    _save_state,
    resolve_default_target,
)

_AGENT_STATE_KEY_SUFFIX = "::agents"  # namespace agent state separately from skills


def bundled_agents_dir() -> Path:
    """Return the Path to the agent definitions bundled inside this package."""
    pkg_dir = Path(__file__).resolve().parent
    agents = pkg_dir / ".agents" / "agents"
    if not agents.is_dir():
        raise FileNotFoundError(
            f"Bundled agents directory not found at {agents}. "
            "Re-install the package to restore it."
        )
    return agents


def _claude_agents_dir(target_root: Path) -> Path:
    """Return ``<target_root>/.claude/agents/`` — Claude Code's discovery path."""
    return target_root / ".claude" / "agents"


def _is_ours(link: Path, src_agents: Path) -> bool:
    """True if *link* is already a symlink into our bundled agents tree."""
    try:
        return link.is_symlink() and link.resolve().parent == src_agents.resolve()
    except OSError:
        return False


def install_agents(target_root: Optional[Path] = None) -> Dict[str, Any]:
    """Symlink every bundled agent ``.md`` into ``<target_root>/.claude/agents/``.

    Idempotent and merge-safe: an agent already symlinked from a previous run
    is left untouched; a name colliding with a pre-existing unrelated agent is
    reported as a conflict and skipped (never overwrites the user's own agent).
    """
    root = Path(target_root) if target_root else Path.cwd()
    dest_root = _claude_agents_dir(root)
    dest_root.mkdir(parents=True, exist_ok=True)

    src_agents = bundled_agents_dir()
    installed = []
    conflicts = []

    for agent_file in sorted(src_agents.glob("*.md")):
        name = agent_file.name
        dest = dest_root / name

        if _is_ours(dest, src_agents):
            installed.append({"name": name, "action": "already_installed"})
            continue
        if not dest.exists():
            dest.symlink_to(agent_file)
            installed.append({"name": name, "action": "linked"})
            continue
        conflicts.append(name)  # a real, non-ours file occupies this name

    return {"target": str(dest_root), "installed": installed, "conflicts": conflicts}


def ensure_agents_setup(
    target_root: Optional[Path] = None, force: bool = False
) -> Dict[str, Any]:
    """Install agents for *target_root* once, tracked in ``~/.dftracer-agents``.

    Called automatically by ``dftracer-mcp-server`` on startup alongside the
    skills setup. Self-heals: the "already done" fast-path is trusted only when
    the symlinks still physically exist, so a deleted ``.claude/agents`` is
    repaired on the next launch rather than being a permanent silent no-op.
    """
    root = Path(target_root).resolve() if target_root else resolve_default_target()
    state = _load_state()
    key = str(root) + _AGENT_STATE_KEY_SUFFIX
    prior = state.get(key)

    bundled_names = sorted(p.name for p in bundled_agents_dir().glob("*.md"))

    if prior and not force and prior.get("bundled_names") == bundled_names:
        dest_root = _claude_agents_dir(root)
        src_agents = bundled_agents_dir()
        links_present = dest_root.is_dir() and all(
            _is_ours(dest_root / n, src_agents) for n in bundled_names
        )
        if links_present:
            return {"status": "already_done", "target": str(dest_root), **prior}
        # else fall through and re-install to repair missing/stale links

    result = install_agents(target_root=root)
    record = {
        "bundled_names": bundled_names,
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": result["target"],
        "conflicts": result["conflicts"],
    }
    state[key] = record
    _save_state(state)
    return {"status": "installed", **record, "actions": result["installed"]}


def main() -> None:
    """Entry point for the ``dftracer-install-agents`` CLI command."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="dftracer-install-agents",
        description=(
            "Symlink the dftracer pipeline subagents from the installed package "
            "into <target>/.claude/agents/ so Claude Code discovers them."
        ),
    )
    parser.add_argument(
        "--target",
        default=None,
        help="Where to install: 'global' (~), 'cwd', or an explicit path. "
        "If omitted, you'll be prompted.",
    )
    parser.add_argument(
        "--list", action="store_true", default=False,
        help="Print the bundled agent names and exit.",
    )
    args = parser.parse_args()

    if args.list:
        for p in sorted(bundled_agents_dir().glob("*.md")):
            print(p.stem)
        return

    choice = args.target
    if choice is None:
        print("Where should dftracer subagents be installed?")
        print("  [1] global  — ~/.claude/agents/     (all your projects)")
        print("  [2] cwd     — ./.claude/agents/      (this project only)")
        print("  [3] other   — specify a path")
        choice = input("Choose [1/2/3] (default 2): ").strip() or "2"
        choice = {"1": "global", "2": "cwd", "3": "other"}.get(choice, choice)
        if choice == "other":
            choice = input("Path: ").strip()

    if choice == "global":
        target_root = Path.home()
    elif choice == "cwd":
        target_root = Path.cwd()
    else:
        target_root = Path(choice).expanduser().resolve()

    result = install_agents(target_root=target_root)
    print(f"Agents directory: {result['target']}")
    for item in result["installed"]:
        marker = "+" if item["action"] == "linked" else "="
        print(f"  {marker} {item['name']}")
    if result["conflicts"]:
        print(f"Skipped {len(result['conflicts'])} name(s) already used:")
        for name in result["conflicts"]:
            print(f"  ! {name}")


if __name__ == "__main__":
    main()
