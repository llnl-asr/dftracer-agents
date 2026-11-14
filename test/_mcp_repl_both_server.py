#!/usr/bin/env python3
import asyncio
import importlib
import importlib.util
from pathlib import Path
from fastmcp import FastMCP

UTILS_SERVER = str(Path(__file__).resolve().parent / "mcp_integration_server.py")

def _load_utils():
    import runpy
    # reuse the build_server from mcp_integration_server
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("_utils_srv", UTILS_SERVER)
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build_server()

def _load_analyzer():
    mod = importlib.import_module("dftracer_agents.mcp_tools.tools.dftracer.dfanalyzer_service")
    svc = mod.DFAnalyzerService()
    return asyncio.run(svc.analyzer_subservice.list_tools())

def main():
    combined = FastMCP("DFTracerAllServicesREPLServer")
    utils_srv = _load_utils()
    for t in asyncio.run(utils_srv.list_tools()):
        combined.add_tool(t)
    for t in _load_analyzer():
        combined.add_tool(t)
    asyncio.run(combined.run_stdio_async(show_banner=False))

if __name__ == "__main__":
    main()
