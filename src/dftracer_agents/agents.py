"""
Install the bundled Claude Code subagent definitions into a discoverable
location.

Subagents are bundled inside the package at
``dftracer_agents/.agents/agents/<name>.md``. Claude Code discovers project
subagents under ``<root>/.claude/agents/<name>.md`` — this module materializes
the bundled agent files there as real copies, resolving each shared
``model: level_N`` placeholder to the concrete Claude model class from
``.agents/workspace/active-models.json`` (Claude Code cannot interpret the
``level_N`` levels that the multi-harness source files carry). The copies are
regenerated whenever the bundled source or the model map changes.

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

import json
import re
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

# Marker injected into every materialized agent so we can recognise our own
# generated files (they are real copies, not symlinks, because the shared
# ``level_N`` model placeholders must be resolved to concrete Claude model
# classes at install time — Claude Code does not understand ``level_N``).
_GEN_MARKER = "# generated-by: dftracer-agents — edit src/dftracer_agents/.agents/agents, not this file"

# Fallback used only if active-models.json is missing/unreadable. Mirrors the
# claude harness map in .agents/workspace/active-models.json.
_DEFAULT_CLAUDE_LEVELS = {
    "level_1": "haiku",
    "level_2": "sonnet",
    "level_3": "sonnet",
    "level_4": "opus",
}


def _load_claude_level_map() -> Dict[str, str]:
    """Return the level_N → Claude model-class map for the claude harness.

    Read from the bundled ``.agents/workspace/active-models.json`` so the
    canonical model map is the single source of truth; falls back to
    ``_DEFAULT_CLAUDE_LEVELS`` if the file is absent or malformed.
    """
    workspace = bundled_agents_dir().parent / "workspace" / "active-models.json"
    try:
        data = json.loads(workspace.read_text())
        mapping = data["harnesses"]["claude"]["class_by_level"]
        # Keep only well-formed string entries; backfill any missing level.
        resolved = dict(_DEFAULT_CLAUDE_LEVELS)
        for level, cls in mapping.items():
            if isinstance(cls, str) and cls:
                resolved[level] = cls
        return resolved
    except (OSError, ValueError, KeyError, TypeError):
        return dict(_DEFAULT_CLAUDE_LEVELS)


def _materialize_agent(text: str, level_map: Dict[str, str]) -> str:
    """Resolve ``model: level_N`` to a concrete Claude class and stamp a marker.

    Only the ``model:`` frontmatter key is rewritten (that is what Claude Code
    reads); ``model_level:`` is left intact as the human-readable level record.
    Agents that already name a concrete class (e.g. ``model: haiku``) are left
    unchanged apart from the generated marker.
    """
    def _sub(match: "re.Match[str]") -> str:
        prefix, value = match.group(1), match.group(2).strip()
        return f"{prefix}{level_map.get(value, value)}"

    text = re.sub(r"(?m)^(model:[ \t]*)(level_[0-9]+)[ \t]*$", _sub, text)

    # Stamp the marker just after the opening frontmatter fence so we can later
    # recognise this file as ours (content-based, since it is a copy now).
    if _GEN_MARKER not in text and text.startswith("---"):
        newline = text.index("\n") + 1
        text = text[:newline] + _GEN_MARKER + "\n" + text[newline:]
    return text


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


def _is_ours(dest: Path, src_agents: Path) -> bool:
    """True if *dest* is a dftracer-managed agent file.

    Recognises both the current form (a materialized copy carrying
    ``_GEN_MARKER``) and the legacy form (a symlink into the bundled tree),
    so an upgrade from the old symlink layout still self-heals cleanly.
    """
    try:
        if dest.is_symlink() and dest.resolve().parent == src_agents.resolve():
            return True
        return dest.is_file() and _GEN_MARKER in dest.read_text()
    except OSError:
        return False


def _is_current(dest: Path, expected: str) -> bool:
    """True if *dest* already holds exactly the materialized content we'd write."""
    try:
        return dest.is_file() and not dest.is_symlink() and dest.read_text() == expected
    except OSError:
        return False


def install_agents(target_root: Optional[Path] = None) -> Dict[str, Any]:
    """Materialize every bundled agent ``.md`` into ``<target_root>/.claude/agents/``.

    Each agent is written as a real file with its ``model: level_N`` placeholder
    resolved to the concrete Claude model class (Claude Code cannot interpret
    ``level_N``). Idempotent and merge-safe: a file already matching what we'd
    write is left untouched; a legacy symlink or a stale copy we own is
    refreshed; a name colliding with an unrelated user file is reported as a
    conflict and never overwritten.
    """
    root = Path(target_root) if target_root else Path.cwd()
    dest_root = _claude_agents_dir(root)
    dest_root.mkdir(parents=True, exist_ok=True)

    src_agents = bundled_agents_dir()
    level_map = _load_claude_level_map()
    installed = []
    conflicts = []

    for agent_file in sorted(src_agents.glob("*.md")):
        name = agent_file.name
        dest = dest_root / name
        content = _materialize_agent(agent_file.read_text(), level_map)

        if _is_current(dest, content):
            installed.append({"name": name, "action": "already_installed"})
            continue
        if dest.exists() and not _is_ours(dest, src_agents):
            conflicts.append(name)  # a real, non-ours file occupies this name
            continue

        action = "refreshed" if (dest.exists() or dest.is_symlink()) else "installed"
        if dest.exists() or dest.is_symlink():
            dest.unlink()  # drop legacy symlink or stale copy before rewriting
        dest.write_text(content)
        installed.append({"name": name, "action": action})

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
        level_map = _load_claude_level_map()
        files_current = dest_root.is_dir() and all(
            _is_current(dest_root / n, _materialize_agent((src_agents / n).read_text(), level_map))
            for n in bundled_names
        )
        if files_current:
            return {"status": "already_done", "target": str(dest_root), **prior}
        # else fall through and re-install to repair missing/stale/outdated files

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
