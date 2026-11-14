"""
Integration tests for the DFTracer Plot MCP service.

Tests run the real tool functions (no mocking) against the sample trace.
Each test verifies the output file is created and the result contains the
expected file path.
"""
from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = str(Path(__file__).resolve().parent / "data" / "cm1_1_48_20240926")


# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------

def _load_plot_module():
    return importlib.import_module("dftracer_agents.mcp_tools.tools.dftracer.dftracer_plot_service")


@pytest.fixture(scope="session")
def plot_module():
    return _load_plot_module()


@pytest.fixture()
def plot_service(plot_module):
    return plot_module.DFTracerPlotService()


def _tool_map(service):
    return {t.name: t.fn for t in asyncio.run(service.plot_subservice.list_tools())}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_tools_registered(plot_service):
    tools = _tool_map(plot_service)
    print(f"\n  registered: {sorted(tools)}")
    assert "plot" in tools
    assert "plot_all" in tools
    print("  plot and plot_all registered  ✓")


def test_service_name(plot_service):
    assert plot_service.name == "dftracer_plot"
    print(f"\n  service.name = {plot_service.name!r}  ✓")


def test_service_registered_in_factory(plot_module):
    svc = plot_module.MCPServiceFactory.get_service("dftracer_plot")
    assert svc is not None
    assert svc.name == "dftracer_plot"
    print("\n  factory['dftracer_plot'] registered  ✓")


# ---------------------------------------------------------------------------
# plot — one test per plot_type + one for html format
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("plot_type", [
    "time_series", "top_files", "top_procs", "io_breakdown", "heatmap"
])
def test_plot_type_produces_file(plot_service, tmp_path, plot_type):
    fn = _tool_map(plot_service)["plot"]
    out = str(tmp_path / f"{plot_type}.png")
    result = fn(
        trace_path=SAMPLE_DIR,
        plot_type=plot_type,
        output_path=out,
        output_format="png",
        max_files=10,
        top_n=5,
    )
    print(f"\n  [{plot_type}] result: {result.splitlines()[1] if len(result.splitlines()) > 1 else result}")
    assert "Plot saved:" in result or "Interactive plot" in result, \
        f"Expected 'Plot saved' in result, got: {result[:200]}"
    assert Path(out).exists(), f"Output file not created: {out}"
    assert Path(out).stat().st_size > 0, "Output file is empty"
    print(f"  file size: {Path(out).stat().st_size:,} bytes  ✓")


def test_plot_html_format(plot_service, tmp_path):
    fn = _tool_map(plot_service)["plot"]
    out = str(tmp_path / "time_series.html")
    result = fn(
        trace_path=SAMPLE_DIR,
        plot_type="time_series",
        output_path=out,
        output_format="html",
        max_files=10,
    )
    print(f"\n  html result: {result.splitlines()[1]}")
    assert "Interactive plot saved" in result
    assert Path(out).exists()
    content = Path(out).read_text()
    assert "<html" in content.lower() or "<!DOCTYPE" in content.lower()
    print(f"  valid HTML ({len(content):,} chars)  ✓")


def test_plot_with_filter_expr(plot_service, tmp_path):
    fn = _tool_map(plot_service)["plot"]
    out = str(tmp_path / "filtered.png")
    result = fn(
        trace_path=SAMPLE_DIR,
        plot_type="top_files",
        filter_expr='name == "write"',
        output_path=out,
        output_format="png",
        max_files=10,
        top_n=5,
    )
    print(f"\n  filtered result: {result.splitlines()[1]}")
    assert "Plot saved:" in result
    assert Path(out).exists()
    print("  filtered plot produced  ✓")


def test_plot_unknown_type_returns_error(plot_service):
    fn = _tool_map(plot_service)["plot"]
    result = fn(trace_path=SAMPLE_DIR, plot_type="galaxy_brain")
    print(f"\n  result: {result!r}")
    assert "Unknown plot_type" in result
    print("  unknown plot_type handled gracefully  ✓")


def test_plot_nonexistent_path_returns_error(plot_service):
    fn = _tool_map(plot_service)["plot"]
    result = fn(trace_path="/nonexistent/trace", plot_type="time_series")
    print(f"\n  result: {result!r}")
    assert "Error" in result
    print("  nonexistent path handled gracefully  ✓")


# ---------------------------------------------------------------------------
# plot_all
# ---------------------------------------------------------------------------

def test_plot_all_creates_five_files(plot_service, tmp_path):
    fn = _tool_map(plot_service)["plot_all"]
    result = fn(
        trace_path=SAMPLE_DIR,
        output_dir=str(tmp_path),
        output_format="png",
        max_files=8,
        top_n=5,
    )
    print(f"\n  plot_all result:\n{result}")
    files = list(tmp_path.glob("*.png"))
    print(f"  files created: {[f.name for f in files]}")
    assert len(files) == 5, f"Expected 5 PNG files, got {len(files)}: {[f.name for f in files]}"
    for f in files:
        assert f.stat().st_size > 0, f"Empty file: {f.name}"
    print(f"  all 5 plots created in {tmp_path}  ✓")
