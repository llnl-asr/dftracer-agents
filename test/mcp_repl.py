#!/usr/bin/env python3
"""
Interactive REPL for the dftracer-agents MCP services.

Starts the MCP integration server as a subprocess over stdio, then drops
into a prompt where you can list tools, inspect their schemas, and call
them with JSON arguments.

Usage:
    python test/mcp_repl.py
    python test/mcp_repl.py --service utils       # dftracer_utils only (default)
    python test/mcp_repl.py --service analyzer    # dfanalyzer + plot
    python test/mcp_repl.py --service both        # dftracer_utils + dfanalyzer + plot (28 tools)

REPL commands:
    list                  — list all available tools
    desc <tool>           — show the full docstring for a tool
    <tool> [<json-args>]  — call a tool; json-args defaults to {}
    quit / exit / Ctrl-C  — exit
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


# ── paths ──────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = Path(sys.executable)   # run from the project venv (.venv)
VENV_BIN    = VENV_PYTHON.parent
THIS_DIR    = Path(__file__).resolve().parent


# ── MCP server entry point ─────────────────────────────────────────────────

# the package server, run as a module by the interpreter below (the dftracer-mcp-server console script)
MCP_SERVER_ARGS = ["-m", "dftracer_agents.mcp_server"]


# ── result formatter ────────────────────────────────────────────────────────

def _format_result(result) -> str:
    parts = []
    for item in result.content:
        if hasattr(item, "text"):
            parts.append(item.text)
        else:
            parts.append(repr(item))
    text = "\n".join(parts)
    if result.isError:
        return f"[ERROR]\n{text}"
    return text


# ── REPL core ───────────────────────────────────────────────────────────────

async def _repl(session: ClientSession) -> None:
    # Load tool list once
    tools_response = await session.list_tools()
    tool_list = tools_response.tools
    tool_map = {t.name: t for t in tool_list}

    # ── welcome banner ──
    print()
    print("=" * 60)
    print("  dftracer-agents MCP REPL")
    print("=" * 60)
    print(f"  {len(tool_list)} tools available.  Type 'list' to see them.")
    print("  Syntax:  <tool_name> [<json-args>]")
    print("  Example: info {\"directory\": \"/path/to/traces\"}")
    print("  Commands: list, desc <tool>, quit")
    print("=" * 60)
    print()

    while True:
        try:
            raw = input("mcp> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not raw:
            continue

        if raw in ("quit", "exit", "q"):
            print("Bye.")
            break

        # ── list ──
        if raw == "list":
            print()
            for t in sorted(tool_list, key=lambda x: x.name):
                first_line = (t.description or "").splitlines()[0][:60]
                print(f"  {t.name:<24}  {first_line}")
            print()
            continue

        # ── desc <tool> ──
        if raw.startswith("desc "):
            name = raw[5:].strip()
            if name not in tool_map:
                print(f"  unknown tool: {name!r}")
                continue
            t = tool_map[name]
            print()
            print(f"Tool: {t.name}")
            print("-" * 60)
            print(t.description or "(no description)")
            if t.inputSchema:
                props = t.inputSchema.get("properties", {})
                required = set(t.inputSchema.get("required", []))
                if props:
                    print()
                    print("Parameters:")
                    for pname, pdef in props.items():
                        req = " (required)" if pname in required else ""
                        ptype = pdef.get("type", "any")
                        default = pdef.get("default", "")
                        default_str = f"  default={default!r}" if default != "" else ""
                        print(f"  {pname}: {ptype}{req}{default_str}")
            print()
            continue

        # ── tool call ──
        parts = raw.split(None, 1)
        tool_name = parts[0]
        args_str = parts[1] if len(parts) > 1 else "{}"

        if tool_name not in tool_map:
            print(f"  unknown tool: {tool_name!r}  (type 'list' to see available tools)")
            continue

        try:
            args = json.loads(args_str)
        except json.JSONDecodeError as e:
            print(f"  invalid JSON args: {e}")
            print(f"  tip: use double quotes, e.g.  {tool_name} {{\"key\": \"value\"}}")
            continue

        print(f"  → calling {tool_name}({args}) ...")
        try:
            result = await session.call_tool(tool_name, args)
            output = _format_result(result)
            print()
            print(output if output.strip() else "(empty output)")
            print()
        except Exception as e:
            print(f"  [exception] {type(e).__name__}: {e}")
            print()


# ── entry point ─────────────────────────────────────────────────────────────

def _server_params(service: str) -> StdioServerParameters:
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "PATH": f"{VENV_BIN}:{os.environ.get('PATH', '')}",
    }
    return StdioServerParameters(
        command=str(VENV_PYTHON),
        args=[*MCP_SERVER_ARGS, "--service", service],
        cwd=str(REPO_ROOT),
        env=env,
    )


async def _main(service: str) -> None:
    params = _server_params(service)
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False) as errlog:
        errlog_path = errlog.name

    print(f"Starting MCP server ({service})…  (server stderr → {errlog_path})")

    with open(errlog_path, "w") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await _repl(session)


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive MCP REPL for dftracer-agents")
    parser.add_argument(
        "--service",
        choices=["utils", "analyzer", "both"],
        default="utils",
        help="Which MCP service to connect to: utils (21 tools), analyzer (5+2 plot), both = all 28 (default: utils)",
    )
    args = parser.parse_args()
    asyncio.run(_main(args.service))


if __name__ == "__main__":
    main()
