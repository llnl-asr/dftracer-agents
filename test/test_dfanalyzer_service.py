from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest


def _load_dfanalyzer_module():
    return importlib.import_module("dftracer_agents.mcp_tools.tools.dftracer.dfanalyzer_service")


@pytest.fixture(scope="session")
def dfanalyzer_module():
    return _load_dfanalyzer_module()


@pytest.fixture()
def dfanalyzer_service(dfanalyzer_module):
    return dfanalyzer_module.DFAnalyzerService()


def _tool_map(service):
    tools = {}
    for t in asyncio.run(service.analyzer_subservice.list_tools()):
        tools[t.name] = t
    return tools


def test_hydra_args_default_shape(dfanalyzer_module):
    # dfanalyzer takes Hydra overrides (key=value), not --flags
    cmd = dfanalyzer_module._hydra_args(trace_path="/tmp/trace")
    assert cmd == ["dfanalyzer", "trace_path=/tmp/trace", "analyzer/preset=generic", "output=console",
                   "cluster=local"]


def test_analyze_tool_registered(dfanalyzer_service):
    tools = _tool_map(dfanalyzer_service)
    assert "analyze" in tools
    assert "list_presets" in tools


def test_analyze_tool_invokes_subprocess_success(monkeypatch, dfanalyzer_module, dfanalyzer_service):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout="ANALYSIS_OK", stderr="")

    monkeypatch.setattr(dfanalyzer_module.subprocess, "run", fake_run)

    tools = _tool_map(dfanalyzer_service)
    analyze_fn = tools["analyze"].fn
    out = analyze_fn(trace_path="/tmp/trace")

    assert out == "ANALYSIS_OK"
    assert calls
    cmd, kwargs = calls[-1]
    assert cmd[0] == "dfanalyzer"
    assert "trace_path=/tmp/trace" in cmd
    assert kwargs.get("capture_output") is True
    assert kwargs.get("text") is True


def test_analyze_tool_invokes_subprocess_failure(monkeypatch, dfanalyzer_module, dfanalyzer_service):
    def fake_run(_cmd, **_kwargs):
        return SimpleNamespace(returncode=2, stdout="oops", stderr="boom")

    monkeypatch.setattr(dfanalyzer_module.subprocess, "run", fake_run)

    tools = _tool_map(dfanalyzer_service)
    analyze_fn = tools["analyze"].fn
    out = analyze_fn(trace_path="/tmp/trace")

    assert "dfanalyzer exited with code 2" in out
    assert "stdout:" in out
    assert "stderr:" in out


def test_service_name_property(dfanalyzer_service):
    assert dfanalyzer_service.name == "dfanalyzer"


def test_service_registered_in_factory(dfanalyzer_module):
    service = dfanalyzer_module.MCPServiceFactory.get_service("dfanalyzer")
    assert service is not None
    assert service.name == "dfanalyzer"
