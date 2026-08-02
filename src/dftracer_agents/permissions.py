"""
Render the harness-neutral permission policy into every harness's native
permission dialect.

The canonical policy is ``src/dftracer_agents/.agents/workspace/permissions.yaml``
(see that file's header for the full per-harness semantics). This module is
the render + install side, mirroring the agents.py / agent_templates.py split:

    claude    <root>/.claude/settings.json   "permissions" key (allow/deny)
    opencode  <root>/.opencode/opencode.jsonc "permission" key (glob map)
    copilot   not applicable — no path/command-scoped permission mechanism;
              the per-agent tools: allowlist is already handled by
              agent_templates.render_copilot
    codex     not applicable from a repo-tracked file — see permissions.yaml
              and agent_templates.render_codex (fixed sandbox_mode) and
              mcp_setup.configure_project_codex (trust advisory)

Only the *packaged templates* under ``.agents/workspace/`` are regenerated
here (git-tracked, fully ours). ``bootstrap.py`` symlinks those templates
into each install target the same way it already does for
``.mcp.json``/``.vscode/mcp.json``/``.opencode/opencode.jsonc`` — so syncing
a project's live permissions is "re-run dftracer-bootstrap-workspace" (or
just follow the existing symlink), not a separate merge step.

CLI usage (after pip install)::

    dftracer-sync-permissions               # re-render the packaged templates

Programmatic usage::

    from dftracer_agents.permissions import sync_permissions
    sync_permissions()
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from dftracer_agents.bootstrap import bundled_workspace_dir
from dftracer_agents.mcp_setup import _strip_jsonc

_MCP_TOOL_DEF_RE = re.compile(r"@[A-Za-z_][A-Za-z0-9_.]*\.tool\(\)\s*def\s+(\w+)\s*\(")


def policy_path(base: Optional[Path] = None) -> Path:
    return (base or bundled_workspace_dir()) / "permissions.yaml"


def load_policy(base: Optional[Path] = None) -> Dict[str, Any]:
    path = policy_path(base)
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path}: policy must be a YAML map")
    return data


def _mcp_tools_dir() -> Path:
    return Path(__file__).resolve().parent / "mcp_tools" / "tools"


def discover_mcp_tool_names() -> List[str]:
    """Static scan for ``@<x>.tool()``-decorated functions — no server boot
    required, so the allowlist this drives can't silently drift from what's
    actually registered the way a hand-maintained list can.

    Matches both the bare ``@mcp.tool()`` form (skills_service.py) and the
    mounted-subservice form ``@self.<name>_subservice.tool()`` most other
    services under mcp_tools/tools/ use — anything ending in ``.tool()``.
    """
    names: List[str] = []
    tools_dir = _mcp_tools_dir()
    if not tools_dir.is_dir():
        return names
    for path in sorted(tools_dir.rglob("*.py")):
        text = path.read_text()
        for match in _MCP_TOOL_DEF_RE.finditer(text):
            names.append(f"mcp__dftracer__{match.group(1)}")
    return sorted(set(names))


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------

def _claude_glob(glob: str) -> str:
    """OpenCode/our policy use ``**``; Claude Code's own dialect (see the
    hand-written settings.json this replaces) uses a single trailing ``*``.
    """
    if glob.endswith("/**"):
        return glob[: -len("/**")] + "/*"
    return glob


def render_claude_permissions(policy: Dict[str, Any], tool_names: List[str]) -> Dict[str, Any]:
    deny: List[str] = [f"Bash({p})" for p in policy.get("bash", {}).get("deny", [])]
    deny += [f"Write({p})" for p in policy.get("deny_write_prefixes", [])]

    allow: List[str] = []
    for entry in policy.get("paths", []):
        if entry.get("mode") != "allow":
            continue
        pattern = _claude_glob(entry["glob"])
        allow.append(f"Edit({pattern})")
        allow.append(f"Write({pattern})")
    allow += [f"Bash({p})" for p in policy.get("bash", {}).get("allow", [])]

    if policy.get("mcp_tools") == "all_dftracer":
        allow += tool_names
    elif isinstance(policy.get("mcp_tools"), list):
        allow += list(policy["mcp_tools"])

    return {"deny": deny, "allow": allow}


def sync_claude_settings(policy: Dict[str, Any], tool_names: List[str], base: Optional[Path] = None) -> str:
    """Targeted key-merge into the packaged settings.json — every other key
    (env, hooks, enabledMcpjsonServers, model) is hand-maintained and left
    untouched.
    """
    path = (base or bundled_workspace_dir()) / ".claude" / "settings.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    new_permissions = render_claude_permissions(policy, tool_names)
    if data.get("permissions") == new_permissions:
        return "already_current"
    data["permissions"] = new_permissions
    path.write_text(json.dumps(data, indent=2) + "\n")
    return "updated"


# ---------------------------------------------------------------------------
# OpenCode
# ---------------------------------------------------------------------------

def render_opencode_permissions(policy: Dict[str, Any]) -> Dict[str, Any]:
    edit: Dict[str, str] = {}
    for entry in policy.get("paths", []):
        edit[entry["glob"]] = entry["mode"]

    bash: Dict[str, str] = {"*": "ask"}
    for pattern in policy.get("bash", {}).get("allow", []):
        bash[pattern] = "allow"
    for pattern in policy.get("bash", {}).get("deny", []):
        bash[pattern] = "deny"

    return {"edit": edit, "bash": bash}


def sync_opencode_config(policy: Dict[str, Any], base: Optional[Path] = None) -> str:
    """Fully regenerates the packaged opencode.jsonc template (schema header,
    mcp block, generated permission block) — this file is entirely ours, not
    a live user config, so there's no comment-preservation concern the way
    there is for a project's own copy (see mcp_setup.configure_opencode).
    """
    path = (base or bundled_workspace_dir()) / ".opencode" / "opencode.jsonc"
    existing = path.read_text() if path.exists() else ""

    # Preserve the existing "mcp" block verbatim — configure_opencode() (in
    # mcp_setup.py) writes a possibly non-default port/host into this same
    # file via the .opencode symlink, and a full regenerate must not clobber
    # that back to the placeholder default.
    default_mcp = {"dftracer": {"type": "remote", "url": "http://127.0.0.1:20000/mcp", "enabled": True}}
    mcp_block = default_mcp
    if existing:
        try:
            parsed = json.loads(_strip_jsonc(existing))
            if isinstance(parsed.get("mcp"), dict):
                mcp_block = parsed["mcp"]
        except json.JSONDecodeError:
            pass

    permission = render_opencode_permissions(policy)
    body = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": mcp_block,
        "permission": permission,
    }
    rendered = (
        "{\n"
        "  // OpenCode project configuration for dftracer-agents\n"
        "  // See docs/harnesses.rst for harness-specific notes.\n"
        "  // Model levels are resolved from:\n"
        "  //   src/dftracer_agents/.agents/workspace/models.yaml\n"
        "  // Permission tiers are resolved from:\n"
        "  //   src/dftracer_agents/.agents/workspace/permissions.yaml\n"
        + json.dumps(body, indent=2)[1:]  # drop the leading "{" — header supplies it
    )
    if rendered == existing:
        return "already_current"
    path.write_text(rendered)
    return "updated"


# ---------------------------------------------------------------------------
# Sync entry point
# ---------------------------------------------------------------------------

def sync_permissions(base: Optional[Path] = None) -> Dict[str, Any]:
    policy = load_policy(base)
    tool_names = discover_mcp_tool_names()
    claude_status = sync_claude_settings(policy, tool_names, base)
    opencode_status = sync_opencode_config(policy, base)
    changed = [s for s in (("claude", claude_status), ("opencode", opencode_status)) if s[1] == "updated"]
    return {
        "claude": claude_status,
        "opencode": opencode_status,
        "mcp_tool_count": len(tool_names),
        "changed": [name for name, _ in changed],
        "summary": (
            f"{len(changed)} harness template(s) updated" if changed else "all permission templates already current"
        ),
    }


def ensure_permissions_setup(target_root: Optional[Path] = None, force: bool = False) -> Dict[str, Any]:
    """Regenerate the packaged permission templates. Called from
    mcp_server.py startup alongside skills/agents/workspace setup — cheap
    (static file scan + two small writes), so no state-file short-circuit is
    needed the way agents/skills installs use one.

    ``target_root``/``force`` are accepted only to match the shared
    ``fn(target_root=..., force=...) -> {"status", "target", ...}`` signature
    the startup loop in mcp_server.py calls every setup step with — this
    step always regenerates the packaged templates themselves (which are
    install-target-independent; bootstrap.py's symlinks are what propagate
    them out to a given ``target_root``).
    """
    result = sync_permissions()
    status = "installed" if result["changed"] or force else "already_done"
    return {"status": status, "target": str(bundled_workspace_dir()), **result}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="dftracer-sync-permissions",
        description=(
            "Re-render the packaged .claude/settings.json permissions block and "
            ".opencode/opencode.jsonc permission block from "
            "src/dftracer_agents/.agents/workspace/permissions.yaml."
        ),
    )
    parser.parse_args()

    result = sync_permissions()
    print(f"Claude:   {result['claude']}")
    print(f"OpenCode: {result['opencode']}")
    print(f"Discovered {result['mcp_tool_count']} mcp__dftracer__* tools")
    print(result["summary"])


if __name__ == "__main__":
    main()
