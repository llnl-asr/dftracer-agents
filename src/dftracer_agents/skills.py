"""
Utilities for locating and installing the dftracer agent skills into a
harness-discoverable location.

Skills are bundled inside the package at ``dftracer_agents/.agents/skills/``.
Claude Code discovers project/personal skills under ``<root>/.claude/skills/
<name>/SKILL.md`` — this module symlinks the bundled skills there (never
copies, so editing the installed package's skills or upgrading the package
is immediately reflected without re-running install).

Goose has no native skill-file convention; it discovers dftracer capability
through the ``skill_list`` / ``skill_search`` / ``skill_load`` MCP tools
exposed by ``dftracer-mcp-server`` instead (see ``dftracer_agents.mcp_setup``
for wiring the MCP server itself into Claude Code / Goose configs).

CLI usage (after pip install)::

    dftracer-install-skills                 # interactive: asks where to install
    dftracer-install-skills --target global # ~/.claude/skills/
    dftracer-install-skills --target cwd    # ./.claude/skills/
    dftracer-install-skills --target /path  # <path>/.claude/skills/
    dftracer-install-skills --list          # print bundled skill names and exit

Programmatic usage::

    from dftracer_agents.skills import bundled_skills_dir, install_skills, ensure_setup
    path = bundled_skills_dir()             # Path to the packaged skills
    install_skills(target_root=Path.home()) # symlink into ~/.claude/skills/
    ensure_setup()                          # idempotent, tracked — used by mcp_server startup
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def bundled_skills_dir() -> Path:
    """Return the Path to the skills bundled inside this package.

    Works for both regular and editable installs.  Raises ``FileNotFoundError``
    if the skills directory is missing from the installation.
    """
    pkg_dir = Path(__file__).resolve().parent
    skills = pkg_dir / ".agents" / "skills"
    if not skills.is_dir():
        raise FileNotFoundError(
            f"Bundled skills directory not found at {skills}. "
            "Re-install the package to restore it."
        )
    return skills


def _claude_skills_dir(target_root: Path) -> Path:
    """Return ``<target_root>/.claude/skills/`` — Claude Code's discovery path."""
    return target_root / ".claude" / "skills"


def _is_ours(link: Path, src_skills: Path) -> bool:
    """True if *link* is already a symlink into our bundled skills tree."""
    try:
        return link.is_symlink() and link.resolve().parent == src_skills.resolve()
    except OSError:
        return False


def install_skills(
    target_root: Optional[Path] = None,
    merge: bool = True,
) -> Dict[str, Any]:
    """Symlink every bundled skill into ``<target_root>/.claude/skills/``.

    Idempotent and merge-safe:
      - A skill already symlinked from a previous run is left untouched.
      - A name that collides with a *pre-existing, unrelated* skill (not one
        of ours) is namespaced as ``dftracer-<name>`` instead of overwriting
        the user's skill. If that namespaced name is also taken, the skill
        is skipped and reported rather than clobbering anything.

    Args:
        target_root: Directory under which ``.claude/skills/`` is created.
            Defaults to the current working directory.
        merge: If False, pre-existing non-ours entries are reported as
            conflicts and skipped without the ``dftracer-`` namespacing
            fallback (use when you want to know about conflicts rather than
            auto-resolve them).

    Returns:
        Dict with ``target`` (the resolved ``.claude/skills/`` path),
        ``installed`` (list of {name, link_name, action}), and ``conflicts``
        (list of skill names that could not be placed).
    """
    root = Path(target_root) if target_root else Path.cwd()
    dest_root = _claude_skills_dir(root)
    dest_root.mkdir(parents=True, exist_ok=True)

    src_skills = bundled_skills_dir()
    installed = []
    conflicts = []

    for skill_dir in sorted(p for p in src_skills.iterdir() if p.is_dir()):
        name = skill_dir.name
        dest = dest_root / name

        if _is_ours(dest, src_skills):
            installed.append({"name": name, "link_name": name, "action": "already_installed"})
            continue

        if not dest.exists():
            dest.symlink_to(skill_dir, target_is_directory=True)
            installed.append({"name": name, "link_name": name, "action": "linked"})
            continue

        # Real conflict: something else occupies this name.
        if not merge:
            conflicts.append(name)
            continue

        namespaced = dest_root / f"dftracer-{name}"
        if _is_ours(namespaced, src_skills):
            installed.append({"name": name, "link_name": namespaced.name, "action": "already_installed"})
            continue
        if not namespaced.exists():
            namespaced.symlink_to(skill_dir, target_is_directory=True)
            installed.append({"name": name, "link_name": namespaced.name, "action": "linked_namespaced"})
            continue

        conflicts.append(name)

    return {"target": str(dest_root), "installed": installed, "conflicts": conflicts}


# ---------------------------------------------------------------------------
# Tracked, idempotent setup — used by `dftracer-mcp-server` on startup so the
# first launch installs skills automatically and later launches are no-ops.
# ---------------------------------------------------------------------------

_STATE_DIR = Path.home() / ".dftracer-agents"
_STATE_FILE = _STATE_DIR / "setup_state.json"


def _looks_like_project(path: Path) -> bool:
    return (path / ".git").exists() or (path / "pyproject.toml").exists()


def resolve_default_target() -> Path:
    """Default install root: the CWD if it looks like a project, else home."""
    cwd = Path.cwd()
    return cwd if _looks_like_project(cwd) else Path.home()


def _load_state() -> Dict[str, Any]:
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_state(state: Dict[str, Any]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2))


def ensure_setup(target_root: Optional[Path] = None, force: bool = False) -> Dict[str, Any]:
    """Install skills for *target_root* exactly once, tracked in ``~/.dftracer-agents``.

    Called automatically by ``dftracer-mcp-server`` on startup. Safe to call
    every launch: after the first successful install for a given target, this
    is a no-op unless the bundled skill set has changed (new/removed skill
    directories) or ``force=True``.

    Never touches MCP client configuration (``.mcp.json``, Claude/Goose
    settings) — only symlinks skill files. Run ``dftracer-configure-mcp``
    separately (and deliberately) to register the MCP server itself.
    """
    root = Path(target_root).resolve() if target_root else resolve_default_target()
    state = _load_state()
    key = str(root)
    prior = state.get(key)

    bundled_names = sorted(p.name for p in bundled_skills_dir().iterdir() if p.is_dir())

    # Trust the "already done" fast-path only if the state matches the current
    # bundled skill set AND the symlinks still physically exist on disk. This
    # self-heals the case where <root>/.claude/skills was deleted or the links
    # went stale after the state file was written — otherwise a valid-looking
    # state entry would make this a silent no-op forever, leaving no skills
    # installed. install_skills() is itself idempotent, so re-running is cheap.
    if prior and not force and prior.get("bundled_names") == bundled_names:
        dest_root = _claude_skills_dir(root)
        src_skills = bundled_skills_dir()
        links_present = dest_root.is_dir() and all(
            _is_ours(dest_root / n, src_skills) or _is_ours(dest_root / f"dftracer-{n}", src_skills)
            for n in bundled_names
        )
        if links_present:
            return {"status": "already_done", "target": key, **prior}
        # else fall through and re-install to repair the missing/stale links

    result = install_skills(target_root=root)
    record = {
        "bundled_names": bundled_names,
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": result["target"],
        "conflicts": result["conflicts"],
    }
    state[key] = record
    _save_state(state)
    return {"status": "installed", "target": key, **record, "actions": result["installed"]}


def main() -> None:
    """Entry point for the ``dftracer-install-skills`` CLI command."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="dftracer-install-skills",
        description=(
            "Symlink the dftracer agent skills from the installed package into "
            "<target>/.claude/skills/ so Claude Code discovers them. Goose has no "
            "skill-file convention — it uses the skill_list/skill_search/skill_load "
            "MCP tools instead; run dftracer-configure-mcp to register the server."
        ),
    )
    parser.add_argument(
        "--target",
        default=None,
        help=(
            "Where to install: 'global' (~), 'cwd' (current directory), or an "
            "explicit path. If omitted, you'll be prompted interactively."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        default=False,
        help="Print the bundled skill names and exit.",
    )
    args = parser.parse_args()

    if args.list:
        try:
            for p in sorted(bundled_skills_dir().iterdir()):
                if p.is_dir():
                    print(p.name)
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    choice = args.target
    if choice is None:
        print("Where should dftracer skills be installed?")
        print("  [1] global  — ~/.claude/skills/           (all your projects)")
        print("  [2] cwd     — ./.claude/skills/            (this project only)")
        print("  [3] other   — specify a path")
        choice = input("Choose [1/2/3] (default 2): ").strip() or "2"
        if choice in ("1", "global"):
            choice = "global"
        elif choice in ("3", "other"):
            choice = input("Path: ").strip()
        else:
            choice = "cwd"

    if choice == "global":
        target_root = Path.home()
    elif choice == "cwd":
        target_root = Path.cwd()
    else:
        target_root = Path(choice).expanduser().resolve()

    try:
        result = install_skills(target_root=target_root)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Skills directory: {result['target']}")
    for item in result["installed"]:
        marker = {"linked": "+", "linked_namespaced": "+", "already_installed": "="}[item["action"]]
        suffix = f" (as {item['link_name']})" if item["link_name"] != item["name"] else ""
        print(f"  {marker} {item['name']}{suffix}")
    if result["conflicts"]:
        print(f"Skipped {len(result['conflicts'])} name(s) already used by other skills:")
        for name in result["conflicts"]:
            print(f"  ! {name}")

    print()
    print("Goose has no skill-file convention — it discovers dftracer capability")
    print("through the skill_list / skill_search / skill_load MCP tools instead.")
    print("Register the MCP server with both harnesses: dftracer-configure-mcp")


if __name__ == "__main__":
    main()
