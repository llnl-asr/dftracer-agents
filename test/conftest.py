from __future__ import annotations

import importlib
import asyncio

import os

import pytest

os.environ.setdefault("DFTRACER_AGENTS_AUTO_PULL", "0")   # tests never pull the real checkouts


def pytest_addoption(parser):
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Run slow integration tests that require network access or long builds",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: marks tests that require network or long builds (enable with --run-slow)",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-slow", default=False):
        skip_slow = pytest.mark.skip(reason="Pass --run-slow to run network integration tests")
        for item in items:
            if item.get_closest_marker("slow"):
                item.add_marker(skip_slow)


SUBSERVICE_ATTRS = [
    "core_subservice",
    "analysis_subservice",
    "query_subservice",
    "utility_subservice",
    "dlio_subservice",
    "synthetic_subservice",
    "mpi_subservice",
]


@pytest.fixture(scope="session")
def service_module():
    """The installed dftracer_utils_service module (src/dftracer_agents/mcp_tools/tools/dftracer/)."""
    return importlib.import_module("dftracer_agents.mcp_tools.tools.dftracer.dftracer_utils_service")


@pytest.fixture()
def service_instance(service_module):
    return service_module.DftracerUtilsService()


@pytest.fixture()
def tool_map_fixture(service_instance):
    return get_tool_map(service_instance)


def get_tool_map(service_instance):
    tool_map = {}
    for attr in SUBSERVICE_ATTRS:
        subservice = getattr(service_instance, attr)
        for tool in asyncio.run(subservice.list_tools()):
            tool_map[tool.name] = tool
    return tool_map


def resolve_command_callable(command):
    for attr in ("fn", "function", "callable", "handler", "_fn"):
        value = getattr(command, attr, None)
        if callable(value):
            return value
    if callable(command):
        return command
    raise TypeError(f"Cannot resolve callable for command object: {command!r}")


SESSION_SUBSERVICES = (
    "session_subservice", "pipeline_subservice", "daemon_subservice", "clang_subservice",
    "annotation_api_subservice", "annotation_subservice", "optimization_subservice",
)


def session_tool_map(session_service) -> dict:
    """Every session-scoped tool the server serves, by name.

    The session tools were one service once; they are now spread over DFTracerSessionService's subservices plus the
    session-aware wrappers of the utils service (session_split_traces, ...) and of the diagnoser service
    (session_diagnose_bottlenecks) -- the same set mcp_server's session + utils + diagnoser builders serve.
    """
    from dftracer_agents.mcp_tools.tools.dftracer import dfdiagnoser_service, dftracer_utils_service

    tools = {}
    subs = [getattr(session_service, n, None) for n in SESSION_SUBSERVICES]
    subs.append(dftracer_utils_service.DftracerUtilsService().session_subservice)
    subs.append(dfdiagnoser_service.DFDiagnoserService().session_subservice)
    for sub in subs:
        if sub is None:
            continue
        for tool in asyncio.run(sub.list_tools()):
            tools.setdefault(tool.name, tool)
    return tools
