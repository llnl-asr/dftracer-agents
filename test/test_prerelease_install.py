"""session_install_dftracer(prebuilt="prerelease"): binary-wheel-only demo install that never compiles."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from dftracer_agents.mcp_tools.tools.session import install as I
from dftracer_agents.mcp_tools.tools.session import session_tools as ST


def _fake_venv(d: Path) -> Path:
    (d / "bin").mkdir(parents=True)
    (d / "pyvenv.cfg").write_text("home = /usr/bin\n")
    py = d / "bin" / "python"
    py.write_text("")
    return py


# ── pip argument handling ────────────────────────────────────────────────────

def test_pip_args_are_binary_only_prerelease():
    args = I._prerelease_pip_args("dftracer==2.2.0.post39.dev0")
    assert args[0] == "install"
    assert "--pre" in args and "--only-binary=:all:" in args
    assert not any("git+" in a or "--no-binary" in a for a in args)
    assert args[-1] == "dftracer==2.2.0.post39.dev0"


@pytest.mark.parametrize("bad", ["git+https://github.com/llnl-asr/dftracer.git@develop", "/tmp/dftracer-src",
                                 "https://example.org/dftracer.tar.gz"])
def test_pip_args_reject_non_index_specs(bad):
    with pytest.raises(ValueError):
        I._prerelease_pip_args(bad)


def test_spec_pins_only_versions():
    assert I._prerelease_spec("develop") == "dftracer"
    assert I._prerelease_spec("") == "dftracer"
    assert I._prerelease_spec("2.2.0.post39.dev0") == "dftracer==2.2.0.post39.dev0"


def test_compile_markers():
    assert I._compile_markers_in("Collecting dftracer\nSuccessfully installed dftracer-2.2.0") == []
    assert I._compile_markers_in("Building wheel for dftracer (pyproject.toml)") == ["Building wheel for"]


# ── installer: no compile, fail fast ─────────────────────────────────────────

def _recording_run(calls, probe_ok=True, install_log="Successfully installed dftracer-2.2.0.post39.dev0"):
    def _run(cmd, cwd=None, env=None, timeout=600):
        calls.append(list(cmd))
        if "download" in cmd:
            if probe_ok:
                d = Path(cmd[cmd.index("-d") + 1])
                (d / "dftracer-2.2.0.post39.dev0-cp313-cp313-manylinux_2_28_x86_64.whl").write_bytes(b"")
                return {"success": True, "returncode": 0, "stdout": "Saved", "stderr": ""}
            return {"success": False, "returncode": 1, "stdout": "",
                    "stderr": "ERROR: No matching distribution found for dftracer"}
        if "index" in cmd:
            return {"success": True, "returncode": 0, "stdout": "dftracer (2.2.0.post39.dev0)", "stderr": ""}
        if "install" in cmd:
            return {"success": True, "returncode": 0, "stdout": install_log, "stderr": ""}
        return {"success": True, "returncode": 0, "stdout": "", "stderr": ""}
    return _run


def test_installer_installs_probed_wheel_without_compiling(tmp_path, monkeypatch):
    py = _fake_venv(tmp_path / "venv")
    calls: list = []
    monkeypatch.setattr(I, "_run", _recording_run(calls))
    r = I._install_dftracer_prerelease_wheel(str(py), ws=tmp_path, pip_index_url="https://pypi.org/simple")
    assert r["success"], r
    installs = [c for c in calls if "install" in c and "uninstall" not in c]
    assert len(installs) == 1
    cmd = installs[0]
    assert "--pre" in cmd and "--only-binary=:all:" in cmd and cmd[-1] == "dftracer==2.2.0.post39.dev0"
    assert not any("git+" in a for c in calls for a in c)
    pre = r["prerelease"]
    assert pre["compiled"] is False and pre["version"] == "2.2.0.post39.dev0"
    assert pre["tags"] == "cp313-cp313-manylinux_2_28_x86_64"
    assert "MPI and HDF5" in pre["message"] and "NOT included" in pre["message"]


def test_installer_fails_fast_when_no_wheel(tmp_path, monkeypatch):
    py = _fake_venv(tmp_path / "venv")
    calls: list = []
    monkeypatch.setattr(I, "_run", _recording_run(calls, probe_ok=False))
    r = I._install_dftracer_prerelease_wheel(str(py), ws=tmp_path)
    assert not r["success"]
    assert not any("install" in c and "uninstall" not in c for c in calls)   # never tried to build/install
    assert "Not compiling" in r["error"] and "only-binary" in r["error"] and "2.2.0.post39.dev0" in r["error"]


def test_installer_rejects_log_with_source_build(tmp_path, monkeypatch):
    py = _fake_venv(tmp_path / "venv")
    monkeypatch.setattr(I, "_run", _recording_run([], install_log="Building wheel for dftracer (pyproject.toml)"))
    r = I._install_dftracer_prerelease_wheel(str(py), ws=tmp_path)
    assert not r["success"] and "never compile" in r["error"]


# ── session_install_dftracer dispatch ────────────────────────────────────────

@pytest.fixture()
def tool(tmp_path, monkeypatch):
    from mcp.server.fastmcp import FastMCP
    monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path / "ws"))
    rid = "demo/20261105_000000"
    (tmp_path / "ws" / rid / "source").mkdir(parents=True)
    _fake_venv(tmp_path / "ws" / rid / "venv")
    monkeypatch.setattr(ST, "_detect_info", lambda *a, **k: {
        "build_tool": "make",
        "features": {"mpi": True, "hdf5": True, "hdf5_system": {"prefix": "/opt/hdf5", "version": "1.14.5"},
                     "hip": True, "hwloc": True},
        "mpi_impl": {"found": True, "compatible": True, "impl": "openmpi", "version": "4.1"},
        "dftracer_pip_env": {"DFTRACER_ENABLE_MPI": "ON", "DFTRACER_ENABLE_HDF5": "ON"}})

    def _no_source_build(**kw):
        raise AssertionError("prerelease must not reach the source/wheelhouse build path")
    monkeypatch.setattr(ST, "_install_dftracer_pip_direct", _no_source_build)
    monkeypatch.setattr(ST, "_find_dftracer_dirs", lambda python_exe=None: {
        "include_dir": "/v/site-packages/dftracer/include", "lib_dir": "/v/site-packages/dftracer/lib64",
        "lib_name": "libdftracer_core.so"})
    seen: dict = {}

    def _fake_prerelease(**kw):
        seen.update(kw)
        return {"success": True, "steps": {"pip_install": {"success": True, "stdout": "", "stderr": ""}},
                "prerelease": {"version": "2.2.0.post39.dev0", "compiled": False,
                               "message": "PREBUILT prerelease; MPI and HDF5 tracing are NOT included"}}
    monkeypatch.setattr(I, "_install_dftracer_prerelease_wheel", _fake_prerelease)
    mcp = FastMCP("t")
    ST.register_session_tools(mcp)
    fn = mcp._tool_manager._tools["session_install_dftracer"].fn
    return fn, rid, seen


def test_rejects_unknown_prebuilt(tool):
    fn, rid, _ = tool
    r = json.loads(fn(run_id=rid, prebuilt="sometimes", app_built_override=True))
    assert r["status"] == "error" and "prerelease" in r["message"]


def test_prerelease_forces_features_off_and_persists_dirs(tool):
    from dftracer_agents.mcp_tools.tools.session.workspace import _load_state
    fn, rid, seen = tool
    r = json.loads(fn(run_id=rid, prebuilt="prerelease", app_built_override=True, mpi=True, hdf5=True))
    assert r["status"] == "ok", r
    assert r["features_enabled"] == []
    assert r["prerelease"]["compiled"] is False and "NOT included" in r["prerelease"]["message"]
    assert r["ref"] == "2.2.0.post39.dev0"
    assert seen["python_exe"].endswith("venv/bin/python")
    st = _load_state(rid)
    assert st["dftracer_build"] == {"mode": "prerelease", "version": "2.2.0.post39.dev0", "mpi": False,
                                    "hdf5": False, "papi": False, "hip": False, "variorum": False}
    assert st["dftracer_pip_include_dir"].endswith("dftracer/include")
    assert st["dftracer_pip_lib_dir"].endswith("dftracer/lib64")
    assert st["dftracer_install_prefix"] == st["dftracer_pip_lib_dir"]
    # detection still describes the APP (it uses MPI), not the installed dftracer
    assert st["detection"]["features"]["mpi"] is True
