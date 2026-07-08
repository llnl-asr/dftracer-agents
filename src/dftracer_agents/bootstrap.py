"""Workspace bootstrap helpers for the dftracer agent harnesses."""
from __future__ import annotations

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
    dest.symlink_to(source, target_is_directory=True)
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

        dest.symlink_to(source)
        return "replaced"

    dest.symlink_to(source)
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

    for relative, source in (
        (".agents/skills", skills_dir),
        (".agents/agents", agents_dir),
        (".opencode/skills", skills_dir),
        (".opencode/agents", agents_dir),
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
