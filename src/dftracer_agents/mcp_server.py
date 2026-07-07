#!/usr/bin/env python3
"""
DFTracer MCP Server — stdio (default) or HTTP transport for Goose and other
MCP clients.

Exposes dftracer_utils and dfanalyzer tools over the Model Context Protocol
using FastMCP's stdio transport (default) or streamable-HTTP.

Usage:
    dftracer-mcp-server                          # stdio mode (default)
    dftracer-mcp-server --transport http         # HTTP on 0.0.0.0:5000
    dftracer-mcp-server --transport http --port 8080
    dftracer-mcp-server --service utils
    dftracer-mcp-server --service analyzer
    dftracer-mcp-server --service both

Claude Code config (.claude/settings.json) for stdio:
    {
      "mcpServers": {
        "dftracer": {
          "command": "dftracer-mcp-server",
          "args": ["--service", "both"]
        }
      }
    }

Claude Code config (.claude/settings.json) for HTTP:
    {
      "mcpServers": {
        "dftracer": { "url": "http://localhost:5000/mcp" }
      }
    }

Goose config (~/.config/goose/config.yaml) for HTTP:
    extensions:
      dftracer:
        type: streamable_http
        uri: http://localhost:5000/mcp
        enabled: true
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from fastmcp import FastMCP

from dftracer_agents.mcp_tools.tools.dftracer import (
    dftracer_utils_service,
    dfanalyzer_service,
    dftracer_plot_service,
    docs_service,
    skills_service,
    dfdiagnoser_service,
    dftracer_service,
)
from dftracer_agents.mcp_tools.tools.papers import academic_service
from dftracer_agents.mcp_tools.tools.system import system_service


# ---------------------------------------------------------------------------
# Server builders
# ---------------------------------------------------------------------------

def _build_utils_server() -> FastMCP:
    service = dftracer_utils_service.DftracerUtilsService()

    server = FastMCP("DFTracerUtils")
    for sub_name in (
        "core_subservice",
        "analysis_subservice",
        "query_subservice",
        "utility_subservice",
        "dlio_subservice",
        "synthetic_subservice",
        "mpi_subservice",
    ):
        sub = getattr(service, sub_name, None)
        if sub is None:
            continue
        for tool in asyncio.run(sub.list_tools()):
            server.add_tool(tool)
    return server


def _build_analyzer_server() -> FastMCP:
    service = dfanalyzer_service.DFAnalyzerService()

    server = FastMCP("DFAnalyzer")
    for tool in asyncio.run(service.analyzer_subservice.list_tools()):
        server.add_tool(tool)
    return server


def _build_plot_server() -> FastMCP:
    service = dftracer_plot_service.DFTracerPlotService()

    server = FastMCP("DFTracerPlot")
    for tool in asyncio.run(service.plot_subservice.list_tools()):
        server.add_tool(tool)
    return server


def _build_docs_server() -> FastMCP:
    service = docs_service.DFTracerDocsService()

    server = FastMCP("DFTracerDocs")
    for tool in asyncio.run(service.docs_subservice.list_tools()):
        server.add_tool(tool)
    return server


def _build_skills_server() -> FastMCP:
    service = skills_service.DFTracerSkillsService()

    server = FastMCP("DFTracerSkills")
    for tool in asyncio.run(service.skills_subservice.list_tools()):
        server.add_tool(tool)
    return server


def _build_diagnoser_server() -> FastMCP:
    service = dfdiagnoser_service.DFDiagnoserService()

    server = FastMCP("DFDiagnoser")
    for tool in asyncio.run(service.diagnoser_subservice.list_tools()):
        server.add_tool(tool)
    return server


def _build_papers_server() -> FastMCP:
    service = academic_service.AcademicPapersService()

    server = FastMCP("AcademicPapers")
    for tool in asyncio.run(service.papers_subservice.list_tools()):
        server.add_tool(tool)
    return server


def _build_system_server() -> FastMCP:
    service = system_service.SystemService()

    server = FastMCP("DFTracerSystem")
    for tool in asyncio.run(service.system_subservice.list_tools()):
        server.add_tool(tool)
    return server


def _build_session_server() -> FastMCP:
    service = dftracer_service.DFTracerSessionService()

    server = FastMCP("DFTracerSession")
    for sub_name in (
        "session_subservice",
        "pipeline_subservice",
        "daemon_subservice",
        "clang_subservice",
        "annotation_api_subservice",
        "annotation_subservice",
        "optimization_subservice",
    ):
        sub = getattr(service, sub_name, None)
        if sub is None:
            continue
        for tool in asyncio.run(sub.list_tools()):
            server.add_tool(tool)
    return server


def build_server(service: str) -> FastMCP:
    """Build and return the combined FastMCP server for the requested service(s)."""
    if service == "utils":
        return _build_utils_server()

    if service == "analyzer":
        combined = FastMCP("DFAnalyzer+Plot+Diagnoser")
        for srv in (_build_analyzer_server(), _build_plot_server(), _build_diagnoser_server()):
            for tool in asyncio.run(srv.list_tools()):
                combined.add_tool(tool)
        return combined

    if service == "diagnoser":
        return _build_diagnoser_server()

    if service == "papers":
        return _build_papers_server()

    if service == "session":
        return _build_session_server()

    if service == "docs":
        return _build_docs_server()

    if service == "skills":
        return _build_skills_server()

    if service == "system":
        return _build_system_server()

    # both — all services
    combined = FastMCP("DFTracer")
    for srv in (
        _build_utils_server(),
        _build_analyzer_server(),
        _build_plot_server(),
        _build_diagnoser_server(),
        _build_session_server(),
        _build_docs_server(),
        _build_papers_server(),
        _build_system_server(),
        _build_skills_server(),
    ):
        for tool in asyncio.run(srv.list_tools()):
            combined.add_tool(tool)
    return combined


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="DFTracer MCP Server — stdio (default) or HTTP transport for Goose and MCP clients"
    )
    parser.add_argument(
        "--service",
        choices=["utils", "analyzer", "session", "docs", "diagnoser", "papers", "system", "skills", "both"],
        default="both",
        help="Which service(s) to expose (default: both)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "streamable-http", "sse"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to for HTTP transports (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="Port to listen on for HTTP transports (default: 5000)",
    )
    parser.add_argument(
        "--path",
        default="/mcp",
        help="URL path for HTTP transports (default: /mcp)",
    )
    parser.add_argument(
        "--skip-setup",
        action="store_true",
        default=False,
        help="Skip the automatic (tracked, idempotent) skill-symlink setup on startup.",
    )
    parser.add_argument(
        "--skills-target",
        default=None,
        help=(
            "Directory under which to install skills into '.claude/skills/'. "
            "Overrides the default (current directory if it looks like a project "
            "-- has .git or pyproject.toml -- otherwise your home directory). "
            "Use this to avoid the silent home-directory fallback when launching "
            "the server from a non-project directory."
        ),
    )
    parser.add_argument(
        "--force-setup",
        action="store_true",
        default=False,
        help=(
            "Re-run skill setup even if the tracking state says it is already "
            "done for this target (also self-heals if the .claude/skills symlinks "
            "were deleted since the last run)."
        ),
    )
    args = parser.parse_args()

    if not args.skip_setup:
        from pathlib import Path as _Path
        from dftracer_agents.skills import ensure_setup, resolve_default_target
        try:
            target_root = (
                _Path(args.skills_target).expanduser().resolve()
                if args.skills_target
                else resolve_default_target()
            )
            result = ensure_setup(target_root=target_root, force=args.force_setup)
            # Always report where skills went and what happened, so a silent
            # "already_done" no-op is never mistaken for "setup didn't run".
            status = result.get("status")
            target = result.get("target", str(target_root))
            if status == "installed":
                print(f"[setup] Skills installed to {target}", file=sys.stderr)
            elif status == "already_done":
                print(
                    f"[setup] Skills already up to date at {target} "
                    f"(use --force-setup to re-link)",
                    file=sys.stderr,
                )
            else:
                print(f"[setup] Skill setup status={status} target={target}", file=sys.stderr)
        except Exception as exc:  # never let setup issues block the server
            import traceback
            print(f"[setup] Skipped skill setup ({type(exc).__name__}): {exc}", file=sys.stderr)
            traceback.print_exc()

    server = build_server(args.service)

    if args.transport == "stdio":
        asyncio.run(server.run_stdio_async(show_banner=False))
    else:
        transport = "streamable-http" if args.transport == "http" else args.transport
        asyncio.run(
            server.run_http_async(
                transport=transport,
                host=args.host,
                port=args.port,
                path=args.path,
                show_banner=True,
            )
        )


if __name__ == "__main__":
    main()
