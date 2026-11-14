"""dftracer_smoke: run the instrumented (or original) application once as a SINGLE process (MPI launchers stripped,
module preamble from the app's own scripts, flux-proxy commands wrapped in a script) and rule deterministically on
that smoke run (dftracer_smoke_check): it passed, stayed single-process, wrote no data to /tmp, printed none of the
known dftracer failure strings, and (when given the trace dir) left non-empty traces.

Tool moved unchanged from dftracer-agents (mcp_tools/tools/session/session_tools.py, register_session_tools).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from dftracer_agents.mcp_tools.tools.session.annotation import (  # moved tools use these helpers unchanged
    _strip_mpi_launcher,
)
from dftracer_agents.mcp_tools.tools.session.session_tools import (  # moved tools use these helpers unchanged
    _build_module_preamble,
    _ensure_flux_proxy_wrapper,
)
from dftracer_agents.mcp_tools.tools.session.workspace import (  # moved tools use these helpers unchanged
    _err,
    _ok,
    _run,
    _save_state,
    _write_artifact_log,
    _ws,
)
import json
import re
import tempfile
from pathlib import Path

from dftracer_agents.mcp_tools.tools.session.workspace import _ws as _cap_ws

# Output that means tracing did not work although the process may have exited 0.
_BAD_OUTPUT = {
    "unable to create log file": "DFTRACER_LOG_FILE points into a directory that does not exist: mkdir -p it first",
    "DFTracer was enabled but not available": "import dftracer.dftracer failed (wrong python / GLIBCXX): fix the env (RULE 0), not the app",
    "Code 2001": "DFTRACER_DATA_DIR not set or not forwarded to every rank: set DFTRACER_DATA_DIR=all and forward it",
    "double free or corruption": "two MPI (or OpenMP) runtimes: build dftracer with the MPI wrappers the APP uses",
    "error while loading shared libraries": "a library is not on LD_LIBRARY_PATH / rpath (libdftracer_core, CCE runtime): add it",
    "GLIBCXX_": "the composed core needs a newer libstdc++ than the python's: preload it or compose fewer layers",
    "HIP Intercept context start failed": "wrong/missing ROCm module or libomp preload; do NOT disable ROCProfiler",
}
_MULTI = re.compile(r"(?:^|\s)(?:-np|-n|--ntasks|--np|-N)\s*=?\s*([0-9]+)")
_LAUNCHER = re.compile(r"\b(?:mpirun|mpiexec|srun|jsrun|aprun)\b|\bflux\s+run\b")


def _add(checks, name, ok, detail, fix=""):
    checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})


def _verdict(checks, **extra):
    status = "inconclusive" if not checks else ("pass" if all(c["ok"] for c in checks) else "fail")
    return {"status": status, "checks": checks, "failed": [c["check"] for c in checks if not c["ok"]], **extra}


def _smoke_check(ws: Path, trace_dir: str = "") -> dict:
    checks: list = []
    if not ws.is_dir():
        _add(checks, "workspace", False, f"unknown run: no workspace at {ws}", "pass the run_id session_create returned")
        return _verdict(checks, workspace=str(ws))
    try:
        state = json.loads((ws / "session.json").read_text())
    except (OSError, ValueError):
        state = {}
    last = state.get("last_smoke_test")
    if not isinstance(last, dict):
        _add(checks, "smoke_recorded", False, "no last_smoke_test in session.json",
             "run session_run_smoke_test(run_id, command, subfolder) (bracketed by session_service_start/stop)")
        return _verdict(checks, workspace=str(ws))
    cmd = str(last.get("command") or "")
    _add(checks, "smoke_passed", bool(last.get("success")) and last.get("returncode", 1) == 0,
         f"returncode={last.get('returncode')}",
         "a failure naming a dftracer symbol or an annotated function is an annotation bug: hand it to the annotator; "
         "any other failure: report it and ask before continuing (rerun the PRISTINE binary to attribute it)")
    many = [int(n) for n in _MULTI.findall(cmd) if int(n) > 1] if _LAUNCHER.search(cmd) else []
    _add(checks, "single_process", not many, f"parallel counts in command: {many}" if many else "single process",
         "smoke tests run ONE process: pass 'mpirun -np 1 <bin>' (the tool strips launchers) and no -j/-n > 1")
    _add(checks, "no_tmp_output", "/tmp/" not in cmd.replace(str(ws), ""), "command writes under /tmp" if "/tmp/" in cmd else "no /tmp path",
         "direct the app's data output to the system-detected PFS (e.g. $LUSTRE_ROOT/$USER/...), never /tmp or home")
    out = f"{last.get('stdout', '')}\n{last.get('stderr', '')}"
    bad = [k for k in _BAD_OUTPUT if k in out]
    _add(checks, "no_dftracer_errors", not bad, f"found: {bad}" if bad else "no known failure strings",
         "; ".join(_BAD_OUTPUT[k] for k in bad))
    if trace_dir:
        td = Path(trace_dir)
        files = sorted(list(td.rglob("*.pfw")) + list(td.rglob("*.pfw.gz"))) if td.is_dir() else []
        empty = [f.name for f in files if f.stat().st_size == 0]
        _add(checks, "traces_written", bool(files) and not empty, f"{len(files)} trace file(s), {len(empty)} empty",
             "no/empty trace: DFTRACER_LOG_FILE dir missing or DFTRACER_DATA_DIR not 'all' (fix those first, keep "
             "DFTRACER_INIT=FUNCTION for annotated binaries); a Fortran binary without a C main may need PRELOAD")
    return _verdict(checks, workspace=str(ws))


def register(mcp):
    """Register the dftracer_smoke tools on *mcp*: the dftracer-agents tools moved here unchanged, then the validator."""

    @mcp.tool()
    def session_run_smoke_test(
        run_id: str,
        command: str,
        subfolder: str = "build",
        env_extra: Optional[str] = None,
        timeout: int = 300,
    ) -> str:
        """Run a smoke test command inside the workspace as a single process.

        Executes *command* without MPI or any parallel launcher to verify that
        the original build is functional before annotation begins.  Any MPI /
        parallel launcher prefix (``mpirun``, ``mpiexec``, ``srun``, ``jsrun``,
        ``aprun``, ``flux run``) is automatically stripped from *command* so
        the binary runs directly.

        This stripping is intentional: smoke tests must be deterministic and
        reproducible without a cluster scheduler or MPI runtime.  The stripped
        command and a boolean ``mpi_launcher_stripped`` flag are both included
        in the response so the agent can audit what was actually executed.

        Side effects:
            * Persists ``{"last_smoke_test": {command, ...subprocess result}}``
              to ``session.json``.
            * Writes an artifact log at step 5.

        Args:
            run_id: Session identifier returned by ``session_create``.
            command: Shell command to execute (passed to ``/bin/sh -c``).
                MPI launchers are stripped automatically — pass the original
                command unchanged; this tool will sanitise it.
            subfolder: Working-directory sub-folder relative to the workspace
                root.  Defaults to ``"build"``.  Falls back to ``"source"``
                if *subfolder* does not exist.
            env_extra: Optional JSON object string (``'{"VAR": "val"}'``) of
                additional environment variables merged into the subprocess
                environment.  Defaults to ``None`` (no extra variables).
            timeout: Seconds before the subprocess is killed.
                Defaults to ``300``.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"`` or ``"error"``).
                * ``message`` — ``"Smoke test passed"`` or ``"Smoke test failed"``.
                * ``command_run`` — the sanitised command that was actually executed.
                * ``mpi_launcher_stripped`` — ``true`` if a launcher prefix was removed.
                * ``stdout``, ``stderr``, ``returncode`` — subprocess output.

        Raises:
            Returns ``{"status": "error"}`` when the command exits non-zero.

        Note:
            Must be called after ``session_build_install``.  A failure here does
            not block the annotation phase — ``session_run_pipeline`` treats a
            failed smoke test as a warning and continues.
        """
        cwd = _ws(run_id) / subfolder
        if not cwd.exists():
            cwd = _ws(run_id) / "source"

        env = {}
        if env_extra:
            env = json.loads(env_extra)

        safe_command, stripped = _strip_mpi_launcher(command)

        # For flux proxy commands, always use a wrapper script — inline bash -c
        # does not propagate module loads reliably inside flux proxy subprocesses.
        safe_command = _ensure_flux_proxy_wrapper(safe_command, _ws(run_id), "run_smoke_test.sh")

        # Prepend module-load preamble extracted from the app's own scripts so
        # the run environment is consistent with what the app's job scripts use.
        preamble = _build_module_preamble(_ws(run_id) / "source")
        if preamble:
            safe_command = preamble + safe_command

        r = _run(["/bin/bash", "-c", safe_command], cwd=cwd, env=env, timeout=timeout)
        _save_state(run_id, {"last_smoke_test": {"command": safe_command, **r}})
        _write_artifact_log(_ws(run_id), 5, "session_run_smoke_test", {
            "command_original": command,
            "command_run": safe_command,
            "mpi_launcher_stripped": stripped,
            "result": r,
        }, run_id)
        if r["success"]:
            return _ok(
                "Smoke test passed",
                command_run=safe_command,
                mpi_launcher_stripped=stripped,
                **r,
            )
        return _err(
            "Smoke test failed",
            command_run=safe_command,
            mpi_launcher_stripped=stripped,
            **r,
        )

    @mcp.tool()
    def dftracer_smoke_check(run_id: str = "", workspace: str = "", trace_dir: str = "") -> dict:
        """Validator for the smoke stage: rule deterministically on the last session_run_smoke_test recorded in
        session.json. Checks it was recorded, exited 0, ran a single process (no -n/-np/--ntasks > 1 in the command),
        wrote nothing under /tmp, and its output has none of the known dftracer failure strings (unable to create log
        file, DFTracer was enabled but not available, Code 2001, double free, missing shared libraries, GLIBCXX, HIP
        intercept); with trace_dir it also requires at least one trace file and no empty one. Args: run_id or workspace
        (path, overrides run_id), trace_dir. Returns {status: pass|fail|inconclusive, checks: [{check, ok, detail, fix}],
        failed, workspace}."""
        if not run_id and not workspace:
            return _verdict([], error="give run_id or workspace")
        return _smoke_check(Path(workspace).expanduser() if workspace else _cap_ws(run_id), trace_dir)

    @mcp.tool()
    def dftracer_smoke_selftest() -> dict:
        """Self-test of dftracer_smoke_check on fixtures in a temporary directory: a clean single-process smoke passes; a
        4-rank smoke writing to /tmp that printed 'Code 2001' fails single_process, no_tmp_output and no_dftracer_errors;
        an empty trace file fails traces_written; no recorded smoke fails smoke_recorded. Returns {status, cases}."""
        cases = []
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d) / "app" / "20260101_000000"
            (ws / "traces").mkdir(parents=True)
            (ws / "traces" / "smoke-1.pfw.gz").write_text("x")
            st = {"last_smoke_test": {"command": "./ior -a POSIX -b 1m -t 1m -s 1 -o $LUSTRE_ROOT/ior/f", "success": True,
                                      "returncode": 0, "stdout": "done", "stderr": ""}}
            (ws / "session.json").write_text(json.dumps(st))
            cases.append({"case": "clean", "want": "pass", "got": _smoke_check(ws, str(ws / "traces"))["status"]})
            st["last_smoke_test"].update(command="mpirun -np 4 ./ior -o /tmp/f", stderr="Code 2001: Data dirs not defined")
            (ws / "session.json").write_text(json.dumps(st))
            cases.append({"case": "bad", "want": ["single_process", "no_tmp_output", "no_dftracer_errors"],
                          "got": _smoke_check(ws)["failed"]})
            (ws / "traces" / "smoke-2.pfw").write_text("")
            st["last_smoke_test"].update(command="./ior", stderr="")
            (ws / "session.json").write_text(json.dumps(st))
            cases.append({"case": "empty_trace", "want": ["traces_written"], "got": _smoke_check(ws, str(ws / "traces"))["failed"]})
            (ws / "session.json").write_text("{}")
            cases.append({"case": "none", "want": ["smoke_recorded"], "got": _smoke_check(ws)["failed"]})
        ok = all(c["want"] == c["got"] for c in cases)
        return {"status": "pass" if ok else "fail", "cases": cases}
