"""Tests for system_service's tracing-feature probes.

These answer "what can dftracer collect on this node" — the PAPI counter
budget, which presets exist and which are derived, which PAPI components are
enabled, whether a power domain exists — and turn that into a counter plan.

The probe parsing is tested against captured `papi_avail` / `papi_component_avail`
output rather than the live node, so the assertions hold on any machine. One
live test runs the real probe and is skipped where PAPI is absent.

The load-bearing property throughout: a *derived* preset is computed from two or
more native events and so costs 2+ of the budget. Sizing a counter set by
counting names undersizes it, and PAPI responds by time-sharing counters and
reporting scaled estimates — with no error and a zero exit code.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from dftracer_agents.mcp_tools.tools.system import system_service as ss  # noqa: E402

# Captured from an MI300A node: 30 presets listed, 5 hardware counters.
PAPI_AVAIL = """\
Available PAPI preset and user defined events plus hardware information.
--------------------------------------------------------------------------------
PAPI version             : 7.2.0.2
Vendor string and code   : AuthenticAMD (2, 0x2)
Number Hardware Counters : 5
Max Multiplex Counters   : 384
--------------------------------------------------------------------------------
    Name        Code    Deriv Description (Note)
PAPI_L1_DCM  0x80000000  No   Level 1 data cache misses
PAPI_L2_DCM  0x80000002  No   Level 2 data cache misses
PAPI_L2_ICM  0x80000003  Yes  Level 2 instruction cache misses
PAPI_TLB_DM  0x80000014  No   Data translation lookaside buffer misses
PAPI_BR_MSP  0x8000002e  No   Conditional branch instructions mispredicted
PAPI_BR_PRC  0x8000002f  Yes  Conditional branch instructions correctly predicted
PAPI_TOT_INS 0x80000032  No   Instructions completed
PAPI_FP_INS  0x80000034  Yes  Floating point instructions
PAPI_TOT_CYC 0x8000003b  No   Total cycles
PAPI_L1_DCA  0x80000040  No   Level 1 data cache accesses
PAPI_FP_OPS  0x80000066  No   Floating point operations
"""

PAPI_COMPONENTS = """\
Available components and hardware information.
Compiled-in components:
Name:   perf_event              Linux perf_event CPU counters
Name:   perf_event_uncore       Linux perf_event CPU uncore and northbridge
   \\-> Disabled: No uncore PMUs or events found
Name:   cray_cassini            HPE Cray Cassini NIC performance counters
Name:   rocp_sdk                GPU events and metrics via AMD ROCprofiler-SDK
   \\-> Disabled: Could not dlopen() librocprofiler-sdk.so.
"""


@pytest.fixture
def papi(monkeypatch) -> dict:
    """probe_papi() against captured output instead of the live node."""
    def fake_shell(script_body: str, timeout: int = 120) -> dict:
        return {
            "rc": 0,
            "stdout": (
                "===PAPI_AVAIL===\n" + PAPI_AVAIL
                + "===PAPI_COMPONENTS===\n" + PAPI_COMPONENTS
                + "===PAPI_WHICH===\n/opt/cray/pe/papi/7.2.0.2/bin/papi_avail\n"
            ),
            "stderr": "",
        }
    monkeypatch.setattr(ss, "_run_login_shell", fake_shell)
    return ss.probe_papi()


def test_probe_parses_budget_presets_and_components(papi: dict) -> None:
    assert papi["available"] is True
    assert papi["version"] == "7.2.0.2"
    assert papi["hardware_counters"] == 5
    assert papi["prefix"] == "/opt/cray/pe/papi/7.2.0.2"
    assert len(papi["presets"]) == 11
    # Deriv column, not a guess from the name.
    assert set(papi["derived_presets"]) == {"PAPI_L2_ICM", "PAPI_BR_PRC", "PAPI_FP_INS"}
    assert papi["presets"]["PAPI_TOT_CYC"]["derived"] is False
    assert papi["components"]["perf_event"]["enabled"] is True
    assert papi["components"]["rocp_sdk"]["enabled"] is False
    # The REASON is what makes a disabled component actionable.
    assert "librocprofiler-sdk" in papi["components"]["rocp_sdk"]["reason"]


def test_probe_reports_absence_rather_than_pretending(monkeypatch) -> None:
    monkeypatch.setattr(
        ss, "_run_login_shell",
        lambda body, timeout=120: {"rc": 127, "stdout": "", "stderr": "papi_avail: not found"},
    )
    result = ss.probe_papi()
    assert result["available"] is False
    assert "not found" in result["error"]


@pytest.mark.parametrize("code_type", sorted(ss._CODE_TYPE_GROUPS))
def test_every_run_fits_the_budget_and_carries_the_reference(papi, code_type) -> None:
    plan = ss.papi_counter_plan(code_type, papi=papi)
    assert plan["error"] is None
    assert plan["runs"], code_type
    for run in plan["runs"]:
        # Derived presets counted at 2, so a name-counting bug shows up here.
        assert run["estimated_cost"] <= plan["budget"], run
        assert run["fits_estimate"] is True
        # Shared reference counter makes runs comparable per-cycle.
        assert ss._PAPI_REFERENCE in run["events"], run
        assert run["env"]["DFTRACER_PAPI_EVENTS"] == ",".join(run["events"])


def test_plan_only_proposes_presets_the_node_actually_has(papi) -> None:
    plan = ss.papi_counter_plan("mixed", papi=papi)
    proposed = {e for run in plan["runs"] for e in run["events"]}
    assert proposed <= set(papi["presets"])
    # ...and says out loud which curated presets this CPU lacks.
    assert "PAPI_FMA_INS" in plan["unavailable_presets"]


def test_mixed_covers_every_available_preset_it_curates(papi) -> None:
    plan = ss.papi_counter_plan("mixed", papi=papi)
    proposed = {e for run in plan["runs"] for e in run["events"]}
    curated = {p for g in ss._PAPI_GROUPS.values() for p in g["presets"]}
    assert proposed == curated & set(papi["presets"])


def test_wrong_instrument_code_types_say_so(papi) -> None:
    for code_type in ("gpu", "communication", "io"):
        plan = ss.papi_counter_plan(code_type, papi=papi)
        assert plan["caveat"], code_type
    assert ss.papi_counter_plan("compute", papi=papi)["caveat"] is None


def test_unknown_code_type_errors_with_the_valid_set(papi) -> None:
    plan = ss.papi_counter_plan("nonsense", papi=papi)
    assert "unknown code_type" in plan["error"]
    assert "compute" in plan["error"]


def test_plan_reports_its_cost_model_as_an_estimate(papi) -> None:
    plan = ss.papi_counter_plan("compute", papi=papi)
    assert "ESTIMATE" in plan["cost_model"]
    assert "multiplex" in plan["verify"]


def test_tiny_budget_still_produces_usable_runs(papi) -> None:
    plan = ss.papi_counter_plan("memory", papi=papi, budget=2)
    assert plan["budget"] == 2
    for run in plan["runs"]:
        assert run["estimated_cost"] <= 2, run


def test_power_probe_shape() -> None:
    power = ss.probe_power()
    assert isinstance(power["sources"], list)
    assert isinstance(power["can_enable"], bool)
    if power["can_enable"]:
        # Enabling power means these exact session_detect flags, and the
        # service-side-only hazard must travel with them.
        assert power["features"]["variorum"] is True
        assert power["features"]["variorum_build"] == "ALWAYS"
        assert any("SERVICE-SIDE" in h for h in power["hazards"])


@pytest.mark.skipif(
    not ss.probe_papi().get("available"),
    reason="PAPI not available on this node",
)
def test_live_probe_agrees_with_the_parser() -> None:
    papi = ss.probe_papi()
    assert papi["hardware_counters"] > 0
    assert papi["presets"]
    plan = ss.papi_counter_plan("mixed", papi=papi)
    for run in plan["runs"]:
        assert run["estimated_cost"] <= papi["hardware_counters"]


def test_runs_without_the_reference_counter_are_flagged(papi) -> None:
    # budget=2 cannot fit a derived preset (cost 2) alongside the reference,
    # so those get a run of their own — which must not silently look
    # normalisable against the runs that do carry it. "branch" is the group
    # that has a derived member (PAPI_BR_PRC) in the captured preset list.
    plan = ss.papi_counter_plan("branch", papi=papi, budget=2)
    for run in plan["runs"]:
        assert run["has_reference"] == (ss._PAPI_REFERENCE in run["events"])
    assert any(not r["has_reference"] for r in plan["runs"])
