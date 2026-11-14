"""session_run_pipeline's resume call (annotation_confirmed=True).

The first call (steps 1-8) pauses after annotation; the second call resumes from the saved state in a fresh call, so
everything steps 9-12 use has to come from session.json or the workspace, not from the first call's locals. It used
to reach the build step with ``smoke_cmd`` and ``src`` undefined (NameError).
"""
from __future__ import annotations

import json

from conftest import session_tool_map


def _resume_workspace(tmp_path, run_id, smoke_command):
    ws = tmp_path / run_id
    (ws / "source").mkdir(parents=True)
    (ws / "annotated").mkdir()
    (ws / "session.json").write_text(json.dumps({
        "run_id": run_id, "workspace": str(ws), "step": "annotation_done",
        # a build tool the resume path has no recipe for: step 9 fails cleanly, so the test stops there
        "detection": {"build_tool": "none", "languages": ["c"], "features": {}},
        "smoke_command": smoke_command,
    }))
    return ws


def _stub_install(monkeypatch):
    """Step 8.5 installs the dftracer prerelease into a session venv: stub it (network, minutes)."""
    from dftracer_agents.mcp_tools.tools.session import install, pipeline_tools

    monkeypatch.setattr(pipeline_tools, "_ensure_session_venv", lambda ws: ws / "venv" / "bin" / "python")
    monkeypatch.setattr(install, "_install_dftracer_prerelease_wheel",
                        lambda **kw: {"success": True, "steps": {}})
    monkeypatch.setattr(install, "_compose_dftracer", lambda *a, **kw: {"success": True, "missing": []})
    monkeypatch.setattr(install, "_ensure_session_env_script", lambda *a, **kw: None)


def _resume(tmp_path, monkeypatch, run_id, **kw):
    from dftracer_agents.mcp_tools.tools.dftracer.dftracer_service import DFTracerSessionService

    monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
    _stub_install(monkeypatch)
    tool = session_tool_map(DFTracerSessionService())["session_run_pipeline"]
    return json.loads(tool.fn(url="file:///unused", run_id=run_id, annotation_confirmed=True, **kw))


def test_resume_reaches_the_build_step(tmp_path, monkeypatch):
    run_id = "app/20261113_000000"
    _resume_workspace(tmp_path, run_id, "echo smoke")
    out = _resume(tmp_path, monkeypatch, run_id)
    assert out["status"] == "error"
    assert out.get("step") == 9, out          # failed building, not on an undefined name before it


def test_resume_without_a_saved_smoke_command(tmp_path, monkeypatch):
    run_id = "app/20261113_000001"
    _resume_workspace(tmp_path, run_id, None)
    out = _resume(tmp_path, monkeypatch, run_id, smoke_test_command="true")
    assert out.get("step") == 9, out

