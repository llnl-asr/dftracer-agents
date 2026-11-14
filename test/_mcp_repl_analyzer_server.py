#!/usr/bin/env python3
"""Stdio MCP server with only the dfanalyzer tools (for test/mcp_repl.py)."""
import asyncio
import importlib

from fastmcp import FastMCP


def _load():
    return importlib.import_module("dftracer_agents.mcp_tools.tools.dftracer.dfanalyzer_service")


def main():
    mod = _load()
    svc = mod.DFAnalyzerService()
    server = FastMCP("DFAnalyzerREPLServer")
    for t in asyncio.run(svc.analyzer_subservice.list_tools()):
        server.add_tool(t)
    asyncio.run(server.run_stdio_async(show_banner=False))


if __name__ == "__main__":
    main()
