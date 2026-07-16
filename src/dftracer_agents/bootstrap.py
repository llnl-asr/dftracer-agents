"""Workspace bootstrap helpers for the dftracer agent harnesses."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from dftracer_agents.agents import bundled_agents_dir
from dftracer_agents.skills import bundled_skills_dir, resolve_default_target


def bundled_workspace_dir() -> Path:
    pkg_dir = Path(__file__).resolve().parent
    workspace = pkg_dir / ".agents" / "workspace"
    if not workspace.is_dir():
        raise FileNotFoundError(f"Bundled workspace instructions directory not found at {workspace}.")
    return workspace


def bundled_memory_dir() -> Path:
    """The packaged, git-tracked memory store.

    Claude Code's per-project memory normally lives in the user's home under
    ``~/.claude/projects/<slug>/memory/``, which is invisible to git and so is
    lost to anyone else installing this package. We keep the real files here and
    symlink the harness path at it, so lessons travel with the source.
    """
    memory = bundled_workspace_dir() / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    return memory


def claude_memory_dir(project_root: Path) -> Path:
    """Where Claude Code looks for this project's memory.

    The slug is the absolute project path with every ``/`` turned into ``-``,
    e.g. ``/usr/WS2/$USER/dftracer-agents`` -> ``-usr-WS2-$USER-dftracer-agents``.
    """
    slug = str(Path(project_root).resolve()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug / "memory"


def _is_ours(link: Path, source: Path) -> bool:
    try:
        return link.is_symlink() and link.resolve() == source.resolve()
    except OSError:
        return False


def _link_dir(dest: Path, source: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _is_ours(dest, source):
        return "already_done"
    if dest.exists():
        return "conflict"
    rel = os.path.relpath(source, dest.parent)
    dest.symlink_to(rel, target_is_directory=True)
    return "installed"


def _link_memory_dir(dest: Path, source: Path) -> str:
    """Point *dest* at the packaged memory store, migrating any files already there.

    Unlike ``_link_dir`` this must not report ``conflict`` when *dest* is a real
    directory: an existing session will have written memories into the harness
    path before it was ever linked. Those files are the whole point, so they are
    moved into *source* (never overwriting a packaged file of the same name)
    before *dest* becomes a symlink.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _is_ours(dest, source):
        return "already_done"

    if dest.is_symlink():
        dest.unlink()
    elif dest.is_dir():
        migrated = 0
        for item in dest.iterdir():
            target = source / item.name
            if not target.exists():
                item.rename(target)
                migrated += 1
            elif item.is_file() and item.read_bytes() == target.read_bytes():
                item.unlink()  # already tracked, byte-identical
        remaining = [p.name for p in dest.iterdir()]
        if remaining:
            # A same-named file whose contents diverge from the packaged one.
            # Refuse rather than silently pick a winner.
            return "conflict"
        dest.rmdir()
        rel = os.path.relpath(source, dest.parent)
        dest.symlink_to(rel, target_is_directory=True)
        return "migrated" if migrated else "installed"
    elif dest.exists():
        return "conflict"

    rel = os.path.relpath(source, dest.parent)
    dest.symlink_to(rel, target_is_directory=True)
    return "installed"


def _link_file(dest: Path, source: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _is_ours(dest, source):
        return "already_done"

    # Always replace pre-existing file/symlink targets.
    # NOTE: Path.exists() is False for broken symlinks, so also test is_symlink().
    if dest.exists() or dest.is_symlink():
        if dest.is_dir() and not dest.is_symlink():
            return "conflict"
        try:
            dest.unlink()
        except OSError:
            return "conflict"

        rel = os.path.relpath(source, dest.parent)
        dest.symlink_to(rel)
        return "replaced"

    rel = os.path.relpath(source, dest.parent)
    dest.symlink_to(rel)
    return "installed"


def ensure_workspace_setup(target_root: Optional[Path] = None, force: bool = False) -> Dict[str, Any]:
    root = Path(target_root).resolve() if target_root else resolve_default_target()
    workspace = bundled_workspace_dir()
    skills_dir = bundled_skills_dir()
    agents_dir = bundled_agents_dir()

    results: Dict[str, Any] = {
        "status": "already_done",
        "target": str(root),
        "instructions": [],
        "links": [],
        "conflicts": [],
    }

    for relative, source_relative in (
        ("AGENTS.md", "AGENTS.md"),
        ("CLAUDE.md", "CLAUDE.md"),
        ("copilot-instructions.md", "copilot-instructions.md"),
        (".github/copilot-instructions.md", "copilot-instructions.md"),
        (".claude/settings.json", ".claude/settings.json"),
        (".opencode/opencode.jsonc", ".opencode/opencode.jsonc"),
        (".vscode/mcp.json", ".vscode/mcp.json"),
        # Claude Code's project-level MCP config. Like the other two, it points at
        # the HTTP server `dftracer_agents_stack` manages, rather than telling the
        # harness to spawn a private stdio copy that bypasses it.
        (".mcp.json", ".mcp.json"),
    ):
        dest = root / relative
        source = workspace / source_relative
        if force and (dest.exists() or dest.is_symlink()) and not dest.is_dir():
            dest.unlink()
        status = _link_file(dest, source)
        results["instructions"].append({"path": str(dest), "status": status, "source": str(source)})
        if status in {"installed", "replaced"}:
            results["status"] = "installed"
        elif status != "already_done":
            results["status"] = "partial"
            results["conflicts"].append(str(dest))

    # NOTE: agent directories are NOT symlinked. The canonical agents are YAML
    # templates (src/dftracer_agents/.agents/agents/*.yaml) that no harness can
    # read directly; ensure_agents_setup renders them into .claude/agents/,
    # .opencode/agents/ and .github/agents/ per harness dialect.
    for relative, source in (
        (".agents/skills", skills_dir),
        (".agents/agents", agents_dir),
        (".opencode/skills", skills_dir),
    ):
        dest = root / relative
        if force and dest.exists() and (dest.is_symlink() or dest.is_file()):
            dest.unlink()
        status = _link_dir(dest, source)
        results["links"].append({"path": str(dest), "status": status, "source": str(source)})
        if status == "installed":
            results["status"] = "installed"
        elif status != "already_done":
            results["status"] = "partial"
            results["conflicts"].append(str(dest))

    # Claude Code's per-project memory. Its path is derived from the project
    # root, not from `root`'s layout, so it cannot join the loop above.
    memory_source = bundled_memory_dir()
    memory_dest = claude_memory_dir(root)
    status = _link_memory_dir(memory_dest, memory_source)
    results["links"].append(
        {"path": str(memory_dest), "status": status, "source": str(memory_source)}
    )
    if status in {"installed", "migrated"}:
        results["status"] = "installed"
    elif status != "already_done":
        results["status"] = "partial"
        results["conflicts"].append(str(memory_dest))

    if results["status"] == "already_done" and results["conflicts"]:
        results["status"] = "partial"

    return results


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="dftracer-bootstrap-workspace",
        description=(
            "Create the shared dftracer workspace instructions and the extra "
            "agent/skill discovery links used by OpenCode and Copilot."
        ),
    )
    parser.add_argument(
        "--target",
        default=None,
        help="Workspace root to bootstrap (default: current project if detected).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Re-run the workspace bootstrap even if files already exist.",
    )
    args = parser.parse_args()

    target_root = Path(args.target).expanduser().resolve() if args.target else None
    result = ensure_workspace_setup(target_root=target_root, force=args.force)
    print(f"Workspace target: {result['target']}")
    for item in result["instructions"]:
        print(f"  {item['status'][0].upper()} instruction {item['path']}")
    for item in result["links"]:
        print(f"  {item['status'][0].upper()} link {item['path']} -> {item['source']}")
    for item in result["conflicts"]:
        print(f"  ! conflict: {item}")
