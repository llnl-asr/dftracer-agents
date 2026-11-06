"""Regression: the utils server must expose the session-aware trace tools.

``session_split_traces`` and ``session_validate_traces`` are defined on
``DftracerUtilsService.session_subservice``. ``_build_utils_server`` once listed
every other subservice but this one, so the combined ("both") server answered
``Unknown tool: 'session_split_traces'`` and callers (rabbit-agent's ropt
instrument/run stages) failed at the trace-split step.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

SESSION_TOOLS = {"session_split_traces", "session_validate_traces"}


def _tool_names(server) -> set:
    return {t.name for t in asyncio.run(server.list_tools())}


def test_utils_server_exposes_session_trace_tools():
    from dftracer_agents.mcp_server import _build_utils_server

    assert SESSION_TOOLS <= _tool_names(_build_utils_server())


def test_combined_server_exposes_session_trace_tools():
    from dftracer_agents.mcp_server import build_server

    assert SESSION_TOOLS <= _tool_names(build_server("both"))
