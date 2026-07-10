"""System detection and configuration MCP tools.

Detects the current HPC/container system from the hostname (stripping trailing
digits), looks it up in ``.dftracer_agents/resources/systems.yaml``, and
returns the module load sequence, environment variables, and MPI launcher for
that system.

Tools
-----
* ``system_detect``  — detect system from hostname; return config
* ``system_list``    — list all known systems in the config file
* ``system_save``    — save or update a system's configuration
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from ...mcp_service_factory import MCPService, MCPServiceFactory

# Path to the systems config relative to the repo root
# __file__ = <root>/src/dftracer_agents/mcp_tools/tools/system/system_service.py
# parents[0]=system/ [1]=tools/ [2]=mcp_tools/ [3]=dftracer_agents/ [4]=src/ [5]=repo root
_REPO_ROOT = Path(__file__).resolve().parents[5]
_SYSTEMS_YAML = _REPO_ROOT / ".dftracer_agents" / "resources" / "systems.yaml"


def _load_yaml_simple(path: Path) -> Dict[str, Any]:
    """Minimal YAML loader for systems.yaml (avoids PyYAML dependency).

    Only handles the specific structure of systems.yaml: top-level keys,
    nested dicts, lists of strings, and string values (including multiline
    block scalars with |). Env var values with ${VAR} are preserved as-is.
    """
    try:
        import yaml  # type: ignore
        with path.open() as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        pass

    # Fallback: hand-rolled parser for the known structure
    if not path.exists():
        return {"systems": {}}

    lines = path.read_text().splitlines()
    result: Dict[str, Any] = {}
    stack: list = [result]
    indent_stack: list = [-1]
    current_list: Optional[list] = None
    current_list_key: Optional[str] = None
    block_scalar_key: Optional[str] = None
    block_scalar_lines: list = []
    block_scalar_indent: int = 0

    for raw_line in lines:
        stripped = raw_line.lstrip()
        if not stripped or stripped.startswith("#"):
            if block_scalar_key is not None:
                block_scalar_lines.append(raw_line.rstrip())
            continue

        indent = len(raw_line) - len(stripped)

        # Collecting block scalar (|)
        if block_scalar_key is not None:
            if indent > block_scalar_indent or stripped.startswith("-"):
                block_scalar_lines.append(raw_line.rstrip())
                continue
            else:
                # End of block scalar
                stack[-1][block_scalar_key] = "\n".join(
                    line[block_scalar_indent:] for line in block_scalar_lines
                ).strip()
                block_scalar_key = None
                block_scalar_lines = []

        # Pop stack frames
        while indent <= indent_stack[-1]:
            indent_stack.pop()
            stack.pop()
            current_list = None

        if stripped.startswith("- "):
            # List item
            if current_list is None:
                current_list = []
                stack[-1][current_list_key] = current_list
            current_list.append(stripped[2:].strip().strip('"').strip("'"))
            continue

        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()

            current_list = None
            current_list_key = key

            if val == "|":
                block_scalar_key = key
                block_scalar_indent = indent + 2
                block_scalar_lines = []
                if indent not in indent_stack:
                    indent_stack.append(indent)
            elif val == "" or val == "{}":
                child: Dict[str, Any] = {}
                stack[-1][key] = child
                stack.append(child)
                indent_stack.append(indent)
            elif val.startswith("["):
                # inline list — skip, use yaml lib if needed
                stack[-1][key] = []
            else:
                val = val.strip('"').strip("'")
                if val.lower() == "true":
                    val = True
                elif val.lower() == "false":
                    val = False
                stack[-1][key] = val

    # Flush trailing block scalar
    if block_scalar_key is not None:
        stack[-1][block_scalar_key] = "\n".join(
            line[block_scalar_indent:] for line in block_scalar_lines
        ).strip()

    return result


def _save_yaml_simple(data: Dict[str, Any], path: Path) -> None:
    """Write systems data back to YAML using PyYAML if available."""
    try:
        import yaml  # type: ignore
        with path.open("w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return
    except ImportError:
        pass

    # Minimal serialiser for the known structure
    lines = [
        "# Known HPC/container system configurations.",
        "# Keyed by the base hostname with trailing digits stripped.",
        "",
        "systems:",
    ]
    for sys_name, cfg in data.get("systems", {}).items():
        lines.append(f"  {sys_name}:")
        for k, v in cfg.items():
            if isinstance(v, bool):
                lines.append(f"    {k}: {'true' if v else 'false'}")
            elif isinstance(v, list):
                lines.append(f"    {k}:")
                for item in v:
                    lines.append(f"      - {item}")
            elif isinstance(v, dict):
                lines.append(f"    {k}:")
                for ek, ev in v.items():
                    lines.append(f"      {ek}: \"{ev}\"")
            elif isinstance(v, str) and "\n" in v:
                lines.append(f"    {k}: |")
                for vline in v.splitlines():
                    lines.append(f"      {vline}")
            else:
                lines.append(f"    {k}: {v}")
    path.write_text("\n".join(lines) + "\n")


def _base_hostname(hostname: Optional[str] = None) -> str:
    """Strip trailing digits from a hostname to get the system base name."""
    h = hostname or socket.gethostname().split(".")[0]
    return re.sub(r"\d+$", "", h)


def get_current_system_env(hostname: Optional[str] = None) -> Dict[str, str]:
    """Return the env dict for the current system, with ${VAR} expanded.

    Looks up ``.dftracer_agents/resources/systems.yaml`` for the detected base hostname and
    resolves any ``${VAR}`` references against the current process
    environment (e.g. ``LD_LIBRARY_PATH: "/opt/x:${LD_LIBRARY_PATH}"``).
    Returns an empty dict if the system is unknown or has no ``env`` section.

    Subprocess-launching tools (dftracer install, annotated build, smoke
    test, trace runs) must merge this into their subprocess env — the MCP
    server process's own environment does not necessarily have these vars
    set, so system-specific paths (e.g. Tuolumne's CCE lib dirs) are silently
    missing unless explicitly re-applied here.
    """
    base = _base_hostname(hostname)
    data = _load_yaml_simple(_SYSTEMS_YAML)
    cfg = (data.get("systems") or {}).get(base)
    if not cfg:
        return {}
    raw_env = cfg.get("env") or {}
    resolved: Dict[str, str] = {}
    for k, v in raw_env.items():
        resolved[k] = re.sub(
            r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), str(v)
        )
    return resolved


def _fmt_system(name: str, cfg: Dict[str, Any]) -> str:
    """Format a system config dict into a human-readable string."""
    lines = [
        f"## System: {name}",
        f"**{cfg.get('description', '')}**",
        f"- sudo available: {cfg.get('sudo', False)}",
        f"- MPI launcher: `{cfg.get('mpi_launcher', 'mpirun')}`",
    ]
    modules = cfg.get("modules", [])
    if modules:
        lines.append("\n### Modules (load in order)")
        for i, m in enumerate(modules, 1):
            lines.append(f"  {i}. {m}")
    env = cfg.get("env", {})
    if env:
        lines.append("\n### Environment variables")
        for k, v in env.items():
            lines.append(f"  `export {k}=\"{v}\"`")
    notes = cfg.get("notes", "").strip()
    if notes:
        lines.append(f"\n### Notes\n{notes}")
    return "\n".join(lines)


def register_system_tools(mcp: FastMCP) -> None:
    """Register all system detection tools onto *mcp*."""

    @mcp.tool()
    def system_detect(hostname: Optional[str] = None) -> str:
        """Detect the current HPC/container system and return its configuration.

        Strips trailing digits from the hostname (e.g. tuolumne1003 →
        tuolumne) to find the system base name, then looks it up in
        .dftracer_agents/resources/systems.yaml. Returns the module load order, environment
        variables, MPI launcher, and any system-specific notes.

        If the system is not recognised, returns an 'unknown' message with
        a prompt to register it via system_save.

        Args:
            hostname: Override the auto-detected hostname. Useful for
                      testing or when called from a login node on behalf of
                      a compute node.

        Returns:
            str: Markdown-formatted system configuration, or an unknown-system
                 message with instructions.
        """
        base = _base_hostname(hostname)
        actual = socket.gethostname()
        data = _load_yaml_simple(_SYSTEMS_YAML)
        systems = data.get("systems", {})

        if base in systems:
            cfg = systems[base]
            header = (
                f"Detected system: **{base}** (from hostname `{actual}`)\n\n"
            )
            return header + _fmt_system(base, cfg)

        # Try container detection as fallback
        is_container = Path("/.dockerenv").exists() or os.path.exists("/run/.containerenv")
        if is_container and "container" in systems:
            cfg = systems["container"]
            header = (
                f"Hostname `{actual}` (base: `{base}`) not in systems.yaml, "
                f"but container environment detected.\n\n"
            )
            return header + _fmt_system("container", cfg)

        known = ", ".join(f"`{k}`" for k in systems)
        return (
            f"System `{base}` (from hostname `{actual}`) is not in systems.yaml.\n\n"
            f"Known systems: {known or 'none'}\n\n"
            f"To register this system, call `system_save` with the system name, "
            f"description, modules, env vars, and MPI launcher.\n\n"
            f"Example:\n"
            f"```\n"
            f"system_save(name=\"{base}\", description=\"...\", sudo=False,\n"
            f"            modules=[\"module1\", \"module2\"],\n"
            f"            env={{\"VAR\": \"value\"}},\n"
            f"            mpi_launcher=\"srun\", notes=\"...\")\n"
            f"```"
        )

    @mcp.tool()
    def system_list() -> str:
        """List all known systems in .dftracer_agents/resources/systems.yaml.

        Returns:
            str: Markdown table of known systems with their descriptions and
                 key attributes.
        """
        data = _load_yaml_simple(_SYSTEMS_YAML)
        systems = data.get("systems", {})
        if not systems:
            return "No systems configured in .dftracer_agents/resources/systems.yaml."
        lines = [
            f"Known systems in `{_SYSTEMS_YAML.relative_to(_REPO_ROOT)}`:\n",
            "| Name | Description | sudo | MPI launcher |",
            "|------|-------------|------|--------------|",
        ]
        for name, cfg in systems.items():
            desc = cfg.get("description", "")
            sudo = "yes" if cfg.get("sudo", False) else "no"
            mpi = cfg.get("mpi_launcher", "mpirun")
            lines.append(f"| `{name}` | {desc} | {sudo} | `{mpi}` |")
        return "\n".join(lines)

    @mcp.tool()
    def system_save(
        name: str,
        description: str = "",
        sudo: bool = False,
        modules: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        mpi_launcher: str = "mpirun",
        notes: str = "",
    ) -> str:
        """Save or update a system's configuration in .dftracer_agents/resources/systems.yaml.

        Call this to register a new system or update an existing one. The
        config is persisted across sessions so future `system_detect` calls
        will recognise it automatically.

        Args:
            name:          Base system name (no digits; e.g. ``"corona"``).
            description:   Short human-readable label.
            sudo:          Whether sudo is available on this system.
            modules:       Ordered list of modules to load before building/running.
            env:           Dict of environment variables to export after module load.
                           Use ``${VAR}`` syntax to reference existing env vars.
            mpi_launcher:  Command used to launch MPI jobs (``"flux run"``,
                           ``"srun"``, ``"mpirun"``).
            notes:         Free-text notes (pitfalls, workarounds, tips).

        Returns:
            str: Confirmation message with the stored config summary.
        """
        data = _load_yaml_simple(_SYSTEMS_YAML)
        if "systems" not in data:
            data["systems"] = {}

        clean_name = re.sub(r"\d+$", "", name.strip())
        data["systems"][clean_name] = {
            "description": description,
            "sudo": bool(sudo),
            "modules": modules or [],
            "env": env or {},
            "mpi_launcher": mpi_launcher,
            "notes": notes,
        }

        _SYSTEMS_YAML.parent.mkdir(parents=True, exist_ok=True)
        _save_yaml_simple(data, _SYSTEMS_YAML)
        return (
            f"Saved system `{clean_name}` to `{_SYSTEMS_YAML.relative_to(_REPO_ROOT)}`.\n\n"
            + _fmt_system(clean_name, data["systems"][clean_name])
        )


class SystemService(MCPService):
    """MCP service for system detection and configuration."""

    def __init__(self) -> None:
        self.system_subservice = FastMCP("DFTracerSystem")
        register_system_tools(self.system_subservice)

    def execute(self, data: dict) -> Optional[str]:
        return "Use system_detect, system_list, or system_save tools."

    @property
    def name(self) -> str:
        return "dftracer-system"


MCPServiceFactory.register("dftracer-system", SystemService())
