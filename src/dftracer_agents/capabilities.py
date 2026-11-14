"""dftracer-agents' capabilities: performance engineering with dftracer, on phronix's capability engine.

The capabilities dftracer-agents owns (``dftracer_*``: install, annotate, build, trace, analyze, diagnose, optimize,
report, ...) live in this package, ``.agents/capabilities/<name>/``, committed with the code. Everything general
(workloads, software, systems, tooling, process) is phronix's, in phronix's bundled layer; dftracer-agents loads its
own over phronix's (``phronix.engine``). moirai-agents and rabbit-agent build on dftracer-agents the same way, through
:func:`chain`.

A change dftracer-agents makes to a capability phronix owns (a lesson, a new version, a new workload_ capability) is
written to its contribution worktree of phronix (``vendor/contrib/phronix``, branch ``contrib/dftracer-agents``) and
committed there; ``capabilities_update`` (or ``python -m phronix.engine update --bootstrap
dftracer_agents.capabilities:engine``) rebases it and says how to open the pull request.

:func:`load_into` adds every enabled capability's tools to a FastMCP server. A capability tool replaces a built-in tool
of the same name (the capability is where that tool now lives). A capability that needs another project's package is
skipped and reported. DFTRACER_AGENTS_CAPABILITY_TOPICS (comma list) limits which phronix topics are served.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

PKG = Path(__file__).resolve().parent
CAP_DIR = PKG / ".agents" / "capabilities"
PROJECT = "dftracer-agents"


def state_dir(*parts: str) -> Path:
    """dftracer-agents' state (lessons, proposals): DFTRACER_AGENTS_HOME, default ~/.dftracer-agents."""
    base = os.environ.get("DFTRACER_AGENTS_HOME") or str(Path.home() / ".dftracer-agents")
    return Path(base).expanduser().joinpath(*parts)


def scope():
    """dftracer-agents' scope: the dftracer_ capabilities, and any capability importing dftracer_agents."""
    from phronix.engine.scopes import Scope
    return Scope(project=PROJECT, package="dftracer_agents", cap_dir=CAP_DIR, prefixes=("dftracer_",))


def chain() -> List:
    """The scopes a project built on dftracer-agents has upstream of it: phronix, then dftracer-agents."""
    from phronix.engine.scopes import phronix_scope
    return [phronix_scope(), scope()]


def engine():
    """phronix's capstore, configured for dftracer-agents (its own capabilities over phronix's)."""
    from phronix.engine import capstore, configure, settings
    from phronix.engine.scopes import phronix_scope
    if not settings.configured or settings.project != PROJECT:
        configure(project=PROJECT, env_prefix="DFTRACER_AGENTS", package_dir=PKG, state_dir=state_dir,
                  module_prefix="dftracer_agents", context_tag="dftracer-context",
                  shared_agent_tools=["lesson_add", "lesson_search", "knowledge_search", "knowledge_read"],
                  bootstrap="dftracer_agents.capabilities:engine", scope=scope(), upstream=[phronix_scope()])
    return capstore


def _topics() -> Optional[set]:
    v = os.environ.get("DFTRACER_AGENTS_CAPABILITY_TOPICS", "").strip()
    return {t.strip() for t in v.split(",") if t.strip()} or None


def collect() -> Dict[str, dict]:
    """name -> {"tools": {tool: {"fn", "schema"}}, "error": str|None, "layer", "dir"} for every enabled capability."""
    S = engine()
    out: Dict[str, dict] = {}
    topics = _topics()
    for name, (layer, d, meta) in sorted(S.all_caps().items()):
        if not meta.get("enabled", True):
            continue
        if topics and layer != PROJECT and Path(d).parent.name not in topics:
            continue
        rec = {"tools": {}, "error": None, "layer": layer, "dir": d}
        code = (Path(d) / "tool.py").read_text() if (Path(d) / "tool.py").exists() else ""
        missing = S.missing_projects(code)
        if missing:
            rec["error"] = f"needs {', '.join(missing)}: another project's capability"
            out[name] = rec
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"dftracer_agents_cap_{name}", Path(d) / "tool.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            shim = S.ToolShim(name)
            mod.register(shim)
            rec["tools"] = shim.tools
        except Exception as e:                       # broken: skip it, never stop the server
            rec["error"] = f"{type(e).__name__}: {e}"
        out[name] = rec
    return out


def _tool_names(server) -> set:
    import asyncio
    lister = getattr(server, "list_tools", None) or getattr(server, "get_tools")
    got = asyncio.run(lister())
    return set(got) if isinstance(got, dict) else {t.name for t in got}


def _remove_tool(server, name: str) -> None:
    """FastMCP 3+ removes through its local provider (server.remove_tool is deprecated there); FastMCP 2 has server.remove_tool."""
    rm = getattr(getattr(server, "local_provider", None), "remove_tool", None) or getattr(server, "remove_tool", None)
    if rm is not None:
        rm(name)


def load_into(server, quiet: bool = False) -> dict:
    """Add the capabilities' tools to a FastMCP server. Returns {"loaded": {cap: [tools]}, "replaced": [...], "skipped": {cap: why}}."""
    loaded, replaced, skipped = {}, [], {}
    existing = _tool_names(server)
    for name, rec in collect().items():
        if rec["error"]:
            skipped[name] = rec["error"]
            continue
        for tool, t in rec["tools"].items():
            if tool in existing:
                try:
                    _remove_tool(server, tool)
                except Exception:          # the add below still replaces it
                    pass
                replaced.append(tool)
            server.tool(name=tool, description=t["schema"].get("description") or None)(t["fn"])
        loaded[name] = sorted(rec["tools"])
    if not quiet:
        print(f"[capabilities] {sum(map(len, loaded.values()))} tools from {len(loaded)} capabilities "
              f"({len(replaced)} replace built-ins, {len(skipped)} not loaded)", file=sys.stderr)
    return {"loaded": loaded, "replaced": sorted(replaced), "skipped": skipped}


def register_contrib_tools(server) -> None:
    """capabilities_contributions / capabilities_update / capabilities_push: what dftracer-agents contributed to phronix,
    rebasing it (conflicts merged), and pushing it (dftracer-agents has no web UI; these are its Rebase and Push buttons)."""

    @server.tool(name="capabilities_contributions")
    def capabilities_contributions() -> dict:
        """What this install has contributed to the projects upstream of it (phronix) and not yet merged. Reads only.

        Returns ``upstream``: per project, ``contribution`` with ``branch`` (contrib/dftracer-agents), ``worktree``,
        ``ahead`` (commits not yet in the upstream), ``commits`` and ``pull_request`` (the link to open one)."""
        engine()
        from phronix.engine import upstream
        return upstream.status()

    @server.tool(name="capabilities_update")
    def capabilities_update() -> dict:
        """Update the upstream clones (fast-forward) and rebase this install's contribution branches on them.

        Use when the user asks to update or rebase. Only the contribution commits are replayed; conflicts in lessons,
        worked entries and capability.json merge by themselves, anything else is merged by the agent (or the rebase is
        aborted and the branch left as it was). Returns per upstream project ``clone_updated``, ``rebase`` (ok,
        up_to_date, conflict, error), ``rebase_resolved``, ``rebase_merged_by_agent``, ``conflicts``,
        ``contribution`` (ahead, commits) and ``to_do`` (push command, pull_request link); capabilities_push pushes."""
        engine()
        from phronix.engine import upstream
        return upstream.update(resolve=True)

    @server.tool(name="capabilities_push")
    def capabilities_push() -> dict:
        """Rebase this install's contribution branches (as capabilities_update, conflicts merged) and push the ones
        with commits, for a pull request. Use when the user asks to push or contribute back.

        Returns per upstream project ``rebase``, ``pushed``, ``push_error`` and ``pull_request`` (give the link to the
        user)."""
        engine()
        from phronix.engine import upstream
        return upstream.push()
