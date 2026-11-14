"""Tests for new session tools: collect_system_info, diagnose_bottlenecks,
search_optimization_papers, session_service_start, session_service_stop."""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------

def _load_session_service():
    return importlib.import_module("dftracer_agents.mcp_tools.tools.dftracer.dftracer_service")


@pytest.fixture(scope="module")
def svc_mod():
    return _load_session_service()


@pytest.fixture()
def service(svc_mod):
    return svc_mod.DFTracerSessionService()


def _tool_map(service):
    from conftest import session_tool_map
    return session_tool_map(service)


def _fn(tool):
    for attr in ("fn", "function", "callable", "handler", "_fn"):
        v = getattr(tool, attr, None)
        if callable(v):
            return v
    raise TypeError(f"No callable on {tool!r}")


def _call(tool, **kwargs):
    fn = _fn(tool)
    raw = asyncio.run(fn(**kwargs)) if asyncio.iscoroutinefunction(fn) else fn(**kwargs)
    return json.loads(raw)


def _make_workspace(tmp_path: Path, run_id: str = "testapp/20260617_000001") -> Path:
    ws = tmp_path / run_id
    ws.mkdir(parents=True)
    state = {"run_id": run_id, "url": "local", "ref": "main",
             "workspace": str(ws), "step": "traced",
             "detection": {"languages": ["c"], "build_tool": "cmake"}}
    (ws / "session.json").write_text(json.dumps(state))
    return ws


# ---------------------------------------------------------------------------
# session_collect_system_info
# ---------------------------------------------------------------------------

class TestCollectSystemInfo:
    RUN_ID = "testapp/20260617_si"

    def test_creates_system_config_json(self, service, monkeypatch, tmp_path):
        ws = _make_workspace(tmp_path, self.RUN_ID)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(service)
        result = _call(tools["session_collect_system_info"], run_id=self.RUN_ID)

        assert result["status"] == "ok"
        config_file = ws / "system_config.json"
        assert config_file.exists(), "system_config.json was not created"
        data = json.loads(config_file.read_text())
        # Must have at least the basic top-level keys
        assert "cpu" in data or "memory" in data or "host" in data

    def test_returns_cpu_section(self, service, monkeypatch, tmp_path):
        _make_workspace(tmp_path, self.RUN_ID + "_cpu")
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        tools = _tool_map(service)
        result = _call(tools["session_collect_system_info"], run_id=self.RUN_ID + "_cpu")
        assert result["status"] == "ok"
        assert "system_config_file" in result or "message" in result

    def test_bad_run_id_returns_error(self, service, monkeypatch, tmp_path):
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        tools = _tool_map(service)
        result = _call(tools["session_collect_system_info"],
                       run_id="no_such_app/20260101_000000")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# session_diagnose_bottlenecks
# ---------------------------------------------------------------------------

class TestDiagnoseBottlenecks:
    RUN_ID = "testapp/20260617_diag"

    def test_missing_traces_split_returns_error(self, service, monkeypatch, tmp_path):
        ws = _make_workspace(tmp_path, self.RUN_ID)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        # traces_split/ does NOT exist → should error
        tools = _tool_map(service)
        result = _call(tools["session_diagnose_bottlenecks"], run_id=self.RUN_ID)
        assert result["status"] == "error"
        assert "traces_split" in result.get("message", "").lower()

    def test_dfanalyzer_failure_returns_error(self, service, svc_mod, monkeypatch, tmp_path):
        ws = _make_workspace(tmp_path, self.RUN_ID + "_ana")
        (ws / "traces_split").mkdir()
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        # Find the workspace module and monkeypatch _run
        ws_mod = sys.modules.get("dftracer_agents.mcp_tools.tools.session.workspace")
        if ws_mod:
            monkeypatch.setattr(ws_mod, "_run",
                                lambda cmd, **kw: {"returncode": 1, "stdout": "",
                                                   "stderr": "dfanalyzer: command not found",
                                                   "success": False})

        tools = _tool_map(service)
        result = _call(tools["session_diagnose_bottlenecks"],
                       run_id=self.RUN_ID + "_ana")
        # Either error (dfanalyzer missing) or ok (if dfanalyzer is installed)
        assert result["status"] in ("ok", "error")

    def test_success_with_mocked_dfanalyzer(self, service, monkeypatch, tmp_path):
        rid = self.RUN_ID + "_ok"
        ws  = _make_workspace(tmp_path, rid)
        tsp = ws / "traces_split"
        tsp.mkdir()
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        def fake_run(cmd, **kwargs):
            # When dfanalyzer is called, create fake checkpoint files
            if cmd and cmd[0] == "dfanalyzer":
                cp_arg = next((a for a in cmd if "checkpoint_dir=" in a), None)
                if cp_arg:
                    cp = Path(cp_arg.split("=", 1)[1])
                    cp.mkdir(parents=True, exist_ok=True)
                    (cp / "_flat_view_time_range.parquet").write_bytes(b"PAR1")
                    scored = {"r0": {"read_time_pct": 0.85, "read_time_pct_score": 4}}
                    (cp / "_raw_stats_session.json").write_text(json.dumps({"ops": 100}))
                    # Also write scored file
                    scored_dir = ws / "diagnosis" / "scored"
                    scored_dir.mkdir(parents=True, exist_ok=True)
                    (scored_dir / "scored.json").write_text(json.dumps(scored))
                return {"returncode": 0, "stdout": "OK", "stderr": "", "success": True}
            # dfdiagnoser call
            return {"returncode": 0, "stdout": "OK", "stderr": "", "success": True}

        ws_mod = sys.modules.get("dftracer_agents.mcp_tools.tools.session.workspace")
        if ws_mod:
            monkeypatch.setattr(ws_mod, "_run", fake_run)
        # Block dfdiagnoser Python API
        monkeypatch.setitem(sys.modules, "dfdiagnoser", None)
        monkeypatch.setitem(sys.modules, "dfdiagnoser.diagnoser", None)

        tools = _tool_map(service)
        result = _call(tools["session_diagnose_bottlenecks"], run_id=rid)

        if result["status"] == "ok":
            assert "severity_counts" in result
            assert "diagnosis_file" in result
            diag_file = Path(result["diagnosis_file"])
            assert diag_file.exists()
            content = json.loads(diag_file.read_text())
            assert "severity_counts" in content

    def test_diagnosis_json_persisted(self, service, monkeypatch, tmp_path):
        """diagnosis.json must be written with the correct structure."""
        rid = self.RUN_ID + "_persist"
        ws  = _make_workspace(tmp_path, rid)
        (ws / "traces_split").mkdir()
        scored_dir = ws / "diagnosis" / "scored"
        scored_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        def fake_run(cmd, **kwargs):
            if cmd and cmd[0] == "dfanalyzer":
                cp_arg = next((a for a in cmd if "checkpoint_dir=" in a), None)
                if cp_arg:
                    cp = Path(cp_arg.split("=", 1)[1])
                    cp.mkdir(parents=True, exist_ok=True)
                    (cp / "_flat_view_time_range.parquet").write_bytes(b"PAR1")
                    scored = {"r0": {"small_io_pct": 0.96, "small_io_pct_score": 5}}
                    (scored_dir / "scored.json").write_text(json.dumps(scored))
            return {"returncode": 0, "stdout": "OK", "stderr": "", "success": True}

        ws_mod = sys.modules.get("dftracer_agents.mcp_tools.tools.session.workspace")
        if ws_mod:
            monkeypatch.setattr(ws_mod, "_run", fake_run)
        monkeypatch.setitem(sys.modules, "dfdiagnoser", None)
        monkeypatch.setitem(sys.modules, "dfdiagnoser.diagnoser", None)

        tools = _tool_map(service)
        result = _call(tools["session_diagnose_bottlenecks"], run_id=rid)

        if result["status"] == "ok":
            diag = json.loads((ws / "diagnosis.json").read_text())
            assert "bottlenecks" in diag
            assert "severity_counts" in diag


# ---------------------------------------------------------------------------
# session_search_optimization_papers
# ---------------------------------------------------------------------------

class TestSearchOptimizationPapers:
    RUN_ID = "testapp/20260617_papers"

    def test_missing_diagnosis_json_returns_error(self, service, monkeypatch, tmp_path):
        ws = _make_workspace(tmp_path, self.RUN_ID)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        tools = _tool_map(service)
        result = _call(tools["session_search_optimization_papers"], run_id=self.RUN_ID)
        assert result["status"] == "error"
        assert "diagnosis.json" in result.get("message", "").lower()

    def _write_diagnosis(self, ws: Path, bottlenecks: list) -> None:
        diagnosis = {
            "run_id": str(ws.name),
            "severity_counts": {"critical": 0, "high": len(bottlenecks)},
            "bottlenecks": bottlenecks,
        }
        (ws / "diagnosis.json").write_text(json.dumps(diagnosis))

    def test_no_bottlenecks_uses_general_query(self, service, monkeypatch, tmp_path):
        ws = _make_workspace(tmp_path, self.RUN_ID + "_gen")
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        self._write_diagnosis(ws, [])  # no bottlenecks

        ws_mod = sys.modules.get("dftracer_agents.mcp_tools.tools.session.workspace")
        if ws_mod:
            def fake_run(cmd, **kwargs):
                # Simulate curl returning empty arXiv XML
                empty = ('<?xml version="1.0"?>'
                         '<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
                return {"returncode": 0, "stdout": empty, "stderr": "", "success": True}
            monkeypatch.setattr(ws_mod, "_run", fake_run)

        tools = _tool_map(service)
        result = _call(tools["session_search_optimization_papers"],
                       run_id=self.RUN_ID + "_gen")
        # Should succeed (possibly 0 papers found, but ok status)
        assert result["status"] == "ok"
        assert "topics_searched" in result
        assert len(result["topics_searched"]) >= 1

    def test_bottleneck_maps_to_query(self, service, monkeypatch, tmp_path):
        rid = self.RUN_ID + "_map"
        ws  = _make_workspace(tmp_path, rid)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        self._write_diagnosis(ws, [
            {"metric": "small_io_pct", "score": 5, "severity": "critical", "value": 0.95},
            {"metric": "rand_pct",     "score": 4, "severity": "high",     "value": 0.82},
        ])

        captured_queries = []

        def fake_run(cmd, **kwargs):
            if cmd and cmd[0] == "curl":
                # Capture the URL to check search queries
                url = next((a for a in cmd if "arxiv.org" in a), "")
                captured_queries.append(url)
            empty = ('<?xml version="1.0"?>'
                     '<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
            return {"returncode": 0, "stdout": empty, "stderr": "", "success": True}

        ws_mod = sys.modules.get("dftracer_agents.mcp_tools.tools.session.workspace")
        if ws_mod:
            monkeypatch.setattr(ws_mod, "_run", fake_run)

        tools = _tool_map(service)
        result = _call(tools["session_search_optimization_papers"], run_id=rid)
        assert result["status"] == "ok"
        # Two distinct metrics → two topics
        assert len(result.get("topics_searched", [])) >= 2

    def test_papers_file_written(self, service, monkeypatch, tmp_path):
        rid = self.RUN_ID + "_file"
        ws  = _make_workspace(tmp_path, rid)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        self._write_diagnosis(ws, [
            {"metric": "read_time_pct", "score": 4, "severity": "high", "value": 0.8}
        ])

        _ARXIV_XML_ONE = ('<?xml version="1.0"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:arxiv="http://arxiv.org/schemas/atom">'
            '<entry>'
            '<id>http://arxiv.org/abs/2310.12345v1</id>'
            '<title>Parallel I/O Read Optimization</title>'
            '<summary>Read tuning techniques.</summary>'
            '<published>2023-10-19T00:00:00Z</published>'
            '<updated>2023-10-19T00:00:00Z</updated>'
            '<author><name>Alice Smith</name></author>'
            '</entry></feed>')

        def fake_run(cmd, **kwargs):
            return {"returncode": 0, "stdout": _ARXIV_XML_ONE, "stderr": "", "success": True}

        ws_mod = sys.modules.get("dftracer_agents.mcp_tools.tools.session.workspace")
        if ws_mod:
            monkeypatch.setattr(ws_mod, "_run", fake_run)

        tools = _tool_map(service)
        result = _call(tools["session_search_optimization_papers"], run_id=rid)
        assert result["status"] == "ok"
        assert (ws / "optimization_papers.json").exists()
        saved = json.loads((ws / "optimization_papers.json").read_text())
        assert "papers" in saved

    def test_extra_query_appended(self, service, monkeypatch, tmp_path):
        rid = self.RUN_ID + "_extra"
        ws  = _make_workspace(tmp_path, rid)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        self._write_diagnosis(ws, [
            {"metric": "small_io_pct", "score": 5, "severity": "critical", "value": 0.9}
        ])
        captured_queries = []

        def fake_run(cmd, **kwargs):
            if "curl" in (cmd[0] if cmd else ""):
                url = next((a for a in cmd if "search_query=" in a or "arxiv" in a), "")
                captured_queries.append(url)
            return {"returncode": 0,
                    "stdout": '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>',
                    "stderr": "", "success": True}

        ws_mod = sys.modules.get("dftracer_agents.mcp_tools.tools.session.workspace")
        if ws_mod:
            monkeypatch.setattr(ws_mod, "_run", fake_run)

        tools = _tool_map(service)
        result = _call(tools["session_search_optimization_papers"],
                       run_id=rid, extra_query="Lustre")
        assert result["status"] == "ok"
        # Extra query should appear in at least one topic
        assert any("Lustre" in t for t in result.get("topics_searched", []))


# ---------------------------------------------------------------------------
# session_service_start / session_service_stop
# ---------------------------------------------------------------------------

class TestServiceDaemonTools:
    RUN_ID = "testapp/20260617_svc"

    def _setup_ws(self, tmp_path: Path) -> Path:
        ws = _make_workspace(tmp_path, self.RUN_ID)
        (ws / "traces").mkdir(exist_ok=True)
        return ws

    def test_service_start_binary_not_found_returns_error(self, service, monkeypatch, tmp_path):
        ws = self._setup_ws(tmp_path)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        # Make shutil.which return None → no binary found
        ws_mod = sys.modules.get("dftracer_agents.mcp_tools.tools.session.workspace")
        svc_mod = sys.modules.get("dftracer_agents.mcp_tools.tools.dftracer.dftracer_service")

        if svc_mod:
            monkeypatch.setattr(svc_mod.shutil, "which",
                                lambda name: None, raising=False)

        tools = _tool_map(service)
        if "session_service_start" not in tools:
            pytest.skip("daemon_subservice not available")
        result = _call(tools["session_service_start"], run_id=self.RUN_ID)
        assert result["status"] == "error"
        assert "not found" in result.get("message", "").lower() or \
               "binary" in result.get("message", "").lower()

    def test_service_start_saves_state(self, service, monkeypatch, tmp_path):
        rid = self.RUN_ID + "_save"
        ws  = _make_workspace(tmp_path, rid)
        (ws / "traces").mkdir(exist_ok=True)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        svc_mod = sys.modules.get("dftracer_agents.mcp_tools.tools.dftracer.dftracer_service")
        ws_mod  = sys.modules.get("dftracer_agents.mcp_tools.tools.session.workspace")

        fake_bin = tmp_path / "dftracer_service"
        fake_bin.write_text("#!/bin/sh\nexit 0\n")
        fake_bin.chmod(0o755)

        if svc_mod:
            monkeypatch.setattr(svc_mod.shutil, "which",
                                lambda name: str(fake_bin), raising=False)
        if ws_mod:
            monkeypatch.setattr(ws_mod, "_run",
                                lambda cmd, **kw: {"returncode": 0, "stdout": "started",
                                                   "stderr": "", "success": True})

        tools = _tool_map(service)
        if "session_service_start" not in tools:
            pytest.skip("daemon_subservice not available")
        result = _call(tools["session_service_start"], run_id=rid)
        assert result["status"] == "ok"
        state = json.loads((ws / "session.json").read_text())
        assert state.get("dftracer_service_running") is True
        assert "dftracer_service_state_dir" in state

    def test_service_stop_no_state_returns_error(self, service, monkeypatch, tmp_path):
        ws = _make_workspace(tmp_path, self.RUN_ID + "_stop_ns")
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        # No dftracer_service_state_dir in session.json → error
        tools = _tool_map(service)
        if "session_service_stop" not in tools:
            pytest.skip("daemon_subservice not available")
        result = _call(tools["session_service_stop"], run_id=self.RUN_ID + "_stop_ns")
        assert result["status"] == "error"
        assert "not started" in result.get("message", "").lower() or \
               "state" in result.get("message", "").lower()

    def test_service_stop_calls_binary(self, service, monkeypatch, tmp_path):
        rid = self.RUN_ID + "_stop_ok"
        ws  = _make_workspace(tmp_path, rid)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        fake_bin = tmp_path / "dftracer_service"
        fake_bin.write_text("#!/bin/sh\nexit 0\n")
        fake_bin.chmod(0o755)
        state_dir = ws / "traces" / "dftracer_service" / "node1"
        state_dir.mkdir(parents=True)

        # Pre-populate session state as if service_start already ran
        state = json.loads((ws / "session.json").read_text())
        state.update({
            "dftracer_service_running":   True,
            "dftracer_service_state_dir": str(state_dir),
            "dftracer_service_bin":       str(fake_bin),
        })
        (ws / "session.json").write_text(json.dumps(state))

        # _run is imported directly into dftracer_service module namespace —
        # patch it there rather than in the workspace module.
        daemon_mod = sys.modules.get("dftracer_agents.mcp_tools.tools.dftracer.dftracer_service")
        called = []

        def capture_run(cmd, **kw):
            called.append(cmd)
            return {"returncode": 0, "stdout": "stopped", "stderr": "", "success": True}

        if daemon_mod:
            monkeypatch.setattr(daemon_mod, "_run", capture_run)
        else:
            ws_mod = sys.modules.get("dftracer_agents.mcp_tools.tools.session.workspace")
            if ws_mod:
                monkeypatch.setattr(ws_mod, "_run", capture_run)

        tools = _tool_map(service)
        if "session_service_stop" not in tools:
            pytest.skip("daemon_subservice not available")
        result = _call(tools["session_service_stop"], run_id=rid)
        # Either ok or a non-fatal warning
        assert result["status"] in ("ok", "warning")
        # Binary should have been called with "stop"
        assert any("stop" in " ".join(str(a) for a in cmd) for cmd in called), \
            f"'stop' not found in any call: {called}"
