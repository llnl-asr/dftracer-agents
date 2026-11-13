"""session_install_dftracer(prebuilt="compose"), the default: the dftracer prerelease with dftracer-modules, then
dftracer-compose links the modules the session needs."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dftracer_agents.mcp_tools.tools.session import install as I
from dftracer_agents.mcp_tools.tools.session import session_tools as ST

VERSION = "2.2.0.post57.dev0"


def _fake_venv(d: Path) -> Path:
    (d / "bin").mkdir(parents=True)
    (d / "pyvenv.cfg").write_text("home = /usr/bin\n")
    py = d / "bin" / "python"
    py.write_text("")
    return py


# ── installer: the modules come with it ──────────────────────────────────────

def test_pip_args_with_modules_are_binary_only():
    args = I._prerelease_pip_args(f"dftracer[modules]=={VERSION}", f"dftracer-modules=={VERSION}")
    assert "--pre" in args and "--only-binary=:all:" in args
    assert args[-2:] == [f"dftracer[modules]=={VERSION}", f"dftracer-modules=={VERSION}"]
    with pytest.raises(ValueError):
        I._prerelease_pip_args(f"dftracer=={VERSION}", "git+https://example.org/x.git")


def test_installer_with_modules_installs_both_at_the_probed_version(tmp_path, monkeypatch):
    py = _fake_venv(tmp_path / "venv")
    calls: list = []

    def _run(cmd, cwd=None, env=None, timeout=600):
        calls.append(list(cmd))
        if "download" in cmd:
            d = Path(cmd[cmd.index("-d") + 1])
            (d / f"dftracer-{VERSION}-cp313-cp313-manylinux_2_28_x86_64.whl").write_bytes(b"")
        return {"success": True, "returncode": 0, "stdout": "Successfully installed", "stderr": ""}
    monkeypatch.setattr(I, "_run", _run)
    r = I._install_dftracer_prerelease_wheel(str(py), ws=tmp_path, with_modules=True)
    assert r["success"], r
    install = [c for c in calls if "install" in c and "uninstall" not in c][0]
    assert install[-2:] == [f"dftracer[modules]=={VERSION}", f"dftracer-modules=={VERSION}"]
    assert "--only-binary=:all:" in install
    uninstall = [c for c in calls if "uninstall" in c][0]
    assert "dftracer-modules" in uninstall
    assert r["prerelease"]["mode"] == "compose" and r["prerelease"]["excluded_features"] == []
    assert "dftracer-compose" in r["prerelease"]["message"]


# ── dftracer-compose ─────────────────────────────────────────────────────────

def _compose_run(calls, pkg: Path, composition: str, ok: bool = True):
    def _run(cmd, cwd=None, env=None, timeout=600):
        calls.append(list(cmd))
        if cmd[0] == "bash":
            (pkg / "lib64" / "dftracer").mkdir(parents=True, exist_ok=True)
            (pkg / "lib64" / "dftracer" / "composition.txt").write_text(composition)
            return {"success": ok, "returncode": 0 if ok else 1, "stdout": "composed", "stderr": "" if ok else "boom"}
        return {"success": True, "returncode": 0, "stdout": str(pkg) + "\n", "stderr": ""}
    return _run


def test_compose_enables_what_is_asked_and_reads_the_composition(tmp_path, monkeypatch):
    calls: list = []
    pkg = tmp_path / "site" / "dftracer"
    env_sh = tmp_path / "env.sh"
    env_sh.write_text("module load openmpi\n")
    monkeypatch.setattr(I, "_run", _compose_run(calls, pkg, "mpi mpi-openmpi-4.1 linked\nhdf5 - absent\n"
                                                             "papi - off\nhwloc - off\nhip rocprof-0.6 missing\ncuda - off\n"))
    r = I._compose_dftracer("/v/bin/python", tmp_path, ["hip", "hdf5", "mpi", "variorum"], jobs=8, env_script=env_sh)
    script = (tmp_path / "scripts" / "compose_dftracer.sh").read_text()
    assert f"source {env_sh}" in script
    assert "-m dftracer.compose --jobs 8 --enable mpi,hdf5,hip --disable papi,hwloc,cuda" in script
    assert r["success"] and r["asked"] == ["mpi", "hdf5", "hip"]
    assert (r["linked"], r["absent"], r["missing"]) == (["mpi"], ["hdf5"], ["hip"])
    assert (tmp_path / "artifacts" / "06c_compose_dftracer.log").exists()


def test_compose_failure_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(I, "_run", _compose_run([], tmp_path / "pkg", "mpi - missing\n", ok=False))
    r = I._compose_dftracer("/v/bin/python", tmp_path, ["mpi"])
    assert not r["success"] and "dftracer-compose failed" in r["error"] and "boom" in r["error"]


def test_compose_nothing_asked_disables_every_module(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(I, "_run", _compose_run(calls, tmp_path / "pkg", "mpi - off\n"))
    I._compose_dftracer("/v/bin/python", tmp_path, [])
    script = (tmp_path / "scripts" / "compose_dftracer.sh").read_text()
    assert "--enable" not in script and "--disable mpi,hdf5,papi,hwloc,hip,cuda" in script


# ── session_install_dftracer: compose is the default ────────────────────────

@pytest.fixture()
def tool(tmp_path, monkeypatch):
    from mcp.server.fastmcp import FastMCP
    monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path / "ws"))
    rid = "demo/20261113_000000"
    (tmp_path / "ws" / rid / "source").mkdir(parents=True)
    _fake_venv(tmp_path / "ws" / rid / "venv")
    monkeypatch.setattr(ST, "_detect_info", lambda *a, **k: {
        "build_tool": "make",
        "features": {"mpi": True, "hdf5": True, "hdf5_system": {"prefix": "/opt/hdf5", "version": "1.14.5"},
                     "hip": True, "hwloc": True},
        "mpi_impl": {"found": True, "compatible": True, "impl": "openmpi", "version": "4.1"},
        "dftracer_pip_env": {"DFTRACER_ENABLE_MPI": "ON", "DFTRACER_ENABLE_HDF5": "ON"}})

    def _no_source_build(**kw):
        raise AssertionError("compose must not reach the source/wheelhouse build path")
    monkeypatch.setattr(ST, "_install_dftracer_pip_direct", _no_source_build)
    monkeypatch.setattr(ST, "_find_dftracer_dirs", lambda python_exe=None: {
        "include_dir": "/v/site-packages/dftracer/include", "lib_dir": "/v/site-packages/dftracer/lib64",
        "lib_name": "libdftracer_core.so"})
    seen: dict = {"compose": None, "linked": ["mpi", "hdf5", "hwloc"], "absent": ["hip"], "missing": []}

    def _fake_prerelease(**kw):
        seen["install"] = kw
        return {"success": True, "steps": {"pip_install": {"success": True, "stdout": "", "stderr": ""}},
                "prerelease": {"version": VERSION, "compiled": False, "mode": "compose",
                               "message": "PREBUILT dftracer prerelease with dftracer-modules"}}

    def _fake_compose(python_exe, ws, want, jobs=4, env_script=None, timeout=3600):
        seen["compose"] = {"want": list(want), "env_script": env_script}
        ok = not seen["missing"]
        return {"success": True, "linked": seen["linked"], "absent": seen["absent"], "missing": seen["missing"],
                "asked": list(want), "composition": [], "script": str(ws / "scripts" / "compose_dftracer.sh"),
                "stdout": "", "stderr": "", **({} if ok else {"error": "hip did not build"})}
    monkeypatch.setattr(I, "_install_dftracer_prerelease_wheel", _fake_prerelease)
    monkeypatch.setattr(I, "_compose_dftracer", _fake_compose)
    mcp = FastMCP("t")
    ST.register_session_tools(mcp)
    fn = mcp._tool_manager._tools["session_install_dftracer"].fn
    return fn, rid, seen


def test_default_installs_with_modules_and_composes_detected_features(tool):
    from dftracer_agents.mcp_tools.tools.session.workspace import _load_state
    fn, rid, seen = tool
    r = json.loads(fn(run_id=rid, app_built_override=True))
    assert r["status"] == "ok", r
    assert seen["install"]["with_modules"] is True
    assert sorted(seen["compose"]["want"]) == ["hdf5", "hip", "hwloc", "mpi"]
    assert str(seen["compose"]["env_script"]).endswith("env.sh")
    assert r["compose"]["linked"] == ["mpi", "hdf5", "hwloc"] and r["compose"]["absent"] == ["hip"]
    assert "Composed: linked mpi, hdf5, hwloc" in r["message"] and "not on this machine: hip" in r["message"]
    assert not any(f == "hip" for f in r["features_enabled"])
    assert {"mpi", "hwloc"} <= set(r["features_enabled"]) and any(f.startswith("hdf5") for f in r["features_enabled"])
    st = _load_state(rid)
    assert st["dftracer_build"]["mode"] == "compose"
    assert (st["dftracer_build"]["mpi"], st["dftracer_build"]["hdf5"], st["dftracer_build"]["hip"]) == (True, True, False)
    assert st["dftracer_build"]["version"] == VERSION


def test_a_module_that_did_not_build_fails_the_install(tool):
    fn, rid, seen = tool
    seen["missing"] = ["hip"]
    seen["linked"], seen["absent"] = ["mpi", "hdf5", "hwloc"], []
    r = json.loads(fn(run_id=rid, app_built_override=True))
    assert r["status"] == "error" and "did not link hip" in r["message"]


def test_arguments_override_detection(tool):
    fn, rid, seen = tool
    json.loads(fn(run_id=rid, app_built_override=True, hip=False, papi=True))
    assert "hip" not in seen["compose"]["want"] and "papi" in seen["compose"]["want"]
