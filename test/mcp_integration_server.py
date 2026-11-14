#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import importlib

from fastmcp import FastMCP


def _load_service_module():
    return importlib.import_module("dftracer_agents.mcp_tools.tools.dftracer.dftracer_utils_service")


def build_server() -> FastMCP:
    module = _load_service_module()
    service = module.DftracerUtilsService()

    combined = FastMCP("DFTracerUtilsIntegrationServer")
    for attr_name in [
        "core_subservice",
        "analysis_subservice",
        "query_subservice",
        "utility_subservice",
        "dlio_subservice",
        "synthetic_subservice",
        "mpi_subservice",
    ]:
        sub = getattr(service, attr_name, None)
        if sub is None:
            continue
        for tool in asyncio.run(sub.list_tools()):
            combined.add_tool(tool)

    return combined


def main() -> None:
    server = build_server()
    asyncio.run(server.run_stdio_async(show_banner=False))


if __name__ == "__main__":
    main()
