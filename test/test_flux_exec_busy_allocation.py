"""A busy auto-detected Flux job must not swallow trace tools (``flux run --exclusive`` would wait for it to end)."""
import subprocess
from types import SimpleNamespace

import pytest

from dftracer_agents.mcp_tools.tools.dftracer import _flux_exec as F


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(F, "_alloc_cache", (0.0, None))
    monkeypatch.delenv("DFTRACER_MCP_DISABLE_FLUX_WRAP", raising=False)
    monkeypatch.delenv("FLUX_JOB_ID", raising=False)
    monkeypatch.setattr(F, "_flux_bin", lambda: "/usr/bin/flux")
    monkeypatch.setattr(F, "_detect_running_allocation", lambda fb: "fJOB")


def _fake_resource(free: str, allocated: str):
    def run(cmd, **kw):
        if "resource" in cmd:
            state = cmd[cmd.index("-s") + 1]
            return SimpleNamespace(returncode=0, stdout={"free": free, "allocated": allocated}[state] + "\n",
                                   stderr="")
        raise AssertionError(f"unexpected command {cmd}")
    return run


def test_ranks_parse():
    assert F._ranks("0-3,7") == {0, 1, 2, 3, 7}
    assert F._ranks("") == set()
    assert F._ranks("garbage") == set()


def test_busy_allocation_is_not_used(monkeypatch):
    # every node of the job runs something (the measured application): run locally, do not queue inside it
    monkeypatch.setattr(F.subprocess, "run", _fake_resource(free="0", allocated="0"))
    assert F.find_active_allocation() is None
    assert F.wrap_for_allocation(["dftracer_comparator"]) == ["dftracer_comparator"]


def test_idle_node_allocation_is_used(monkeypatch):
    monkeypatch.setattr(F.subprocess, "run", _fake_resource(free="0-3", allocated="1-2"))
    assert F.find_active_allocation() == "fJOB"


def test_auto_detected_failure_falls_back_to_local(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "/usr/bin/flux":
            raise subprocess.CalledProcessError(1, cmd)
        return SimpleNamespace(returncode=0, stdout="local", stderr="")
    monkeypatch.setattr(F, "find_active_allocation", lambda: "fJOB")
    monkeypatch.setattr(F.subprocess, "run", run)
    assert F.run_on_allocation(["tool"], check=True).stdout == "local"
    assert calls[-1] == ["tool"]


def test_explicit_allocation_failure_is_raised(monkeypatch):
    def run(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(F.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        F.run_on_allocation(["tool"], allocation_id="fMINE", check=True)
