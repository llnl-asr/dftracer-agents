"""dftracer_isolation: verify that a reproduction package (final_report/, final_provenance/ ...) runs in isolation -
a fresh copy, a clean login shell (env -i bash -l), only its config.ini filled in - with a static lint, a detached
launch outside the session tree, and status polling; and rule deterministically on the outcome
(dftracer_isolation_check).

Tool moved unchanged from dftracer-agents (mcp_tools/tools/session/isolation.py, register_isolation_tools).
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

from dftracer_agents.mcp_tools.tools.session.isolation import (  # moved tools use these helpers unchanged
    STATE,
    _static,
)
from dftracer_agents.mcp_tools.tools.session.workspace import (  # moved tools use these helpers unchanged
    _err,
    _ok,
    _ws,
)
import json
import tempfile
from pathlib import Path

from dftracer_agents.mcp_tools.tools.session.isolation import STATE as _ISO_STATE, _static as _iso_static
from dftracer_agents.mcp_tools.tools.session.workspace import _ws as _cap_ws


def _add(checks, name, ok, detail, fix=""):
    checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})


def _verdict(checks, **extra):
    status = "inconclusive" if not checks else ("pass" if all(c["ok"] for c in checks) else "fail")
    return {"status": status, "checks": checks, "failed": [c["check"] for c in checks if not c["ok"]], **extra}


def _isolation_check(ws: Path, package_dir: str, loader: str, entry: str, require_launch: bool) -> dict:
    checks: list = []
    if not ws.is_dir():
        _add(checks, "workspace", False, f"unknown run: no workspace at {ws}", "pass the run_id session_create returned")
        return _verdict(checks, workspace=str(ws))
    pkg = (ws / package_dir).resolve()
    if not pkg.is_dir():
        _add(checks, "package", False, f"package not found: {pkg}", "assemble it first (session_final_report) or pass package_dir")
        return _verdict(checks, workspace=str(ws))
    st = _iso_static(pkg, ws, loader, entry)
    _add(checks, "static_clean", st["clean"], f"{st['scripts_checked']} scripts; findings: {st['findings'][:8]}",
         "fix every finding (no workspace, home or user paths, no flux job ids, every script sources the loader, every "
         "required config.ini key set, bash -n passes), then session_verify_isolation(mode='static') again")
    sp = ws / "artifacts" / _ISO_STATE
    if not sp.is_file():
        if require_launch:
            _add(checks, "launched", False, "no isolation launch recorded",
                 "session_verify_isolation(run_id, mode='launch', isolation_root=<outside the session, on the PFS>)")
        return _verdict(checks, workspace=str(ws), package=str(pkg))
    s = json.loads(sp.read_text())
    iso = Path(s.get("iso_dir", ""))
    _add(checks, "outside_session", not str(iso).startswith(str(ws.resolve())), f"iso_dir={iso}",
         "isolation_root must be outside the session workspace")
    ec = iso / "exit_code"
    done = ec.is_file()
    _add(checks, "finished", done, "finished" if done else "still running or killed (no exit_code)",
         "poll session_verify_isolation(mode='status'); a run that never writes exit_code was killed: relaunch")
    if done:
        code = (ec.read_text().strip() or "-1")
        _add(checks, "exit_zero", code == "0", f"exit_code={code}",
             "read the log tail (mode='status'): a missing module/venv/config key in the clean shell is the usual cause; "
             "fix the package, not the clean environment")
    return _verdict(checks, workspace=str(ws), package=str(pkg))


def register(mcp):
    """Register the dftracer_isolation tools on *mcp*: the dftracer-agents tools moved here unchanged, then the validator."""

    @mcp.tool()
    def session_verify_isolation(run_id: str, package_dir: str = "final_report", mode: str = "static",
                                 isolation_root: str = "", entry: str = "scripts/run_all.sh",
                                 entry_args: str = "", loader: str = "lib_load_config.sh",
                                 env_extra: Dict[str, str] | None = None, tail_lines: int = 40) -> str:
        """Verify a reproduction package runs in isolation (fresh copy, clean login shell, config.ini only).

        Args:
            run_id: session id.
            package_dir: package path relative to the session workspace (final_report, final_provenance, ...).
            mode: "static" (lint, no execution), "launch" (copy to isolation_root/<ts>/pkg and run
                entry detached under env -i bash -l, OUTPUT_ROOT=<that dir>/out), "status" (poll a launch).
            isolation_root: parent dir for the isolated copy; must be OUTSIDE the session workspace
                (use the PFS, e.g. $LUSTRE_ROOT/$USER). Required for launch.
            entry: script run by launch, relative to the package.
            entry_args: arguments for the entry script (e.g. "2n").
            loader: config loader every script must source.
            env_extra: extra variables for the clean environment (e.g. {"REUSE_VENV": "1", "VENV": ...}).
            tail_lines: log lines returned by status.
        """
        ws = _ws(run_id)
        pkg = (ws / package_dir).resolve()
        if not pkg.is_dir():
            return _err(f"package not found: {pkg}")
        state_p = ws / "artifacts" / STATE
        if mode == "static":
            return _ok("static isolation check", package=str(pkg), **_static(pkg, ws, loader, entry))
        if mode == "launch":
            st = _static(pkg, ws, loader, entry)
            if not st["clean"]:
                return _err("fix static findings before launching", findings=st["findings"])
            if not isolation_root:
                return _err("isolation_root is required (outside the session workspace)")
            root = Path(os.path.expandvars(isolation_root)).resolve()
            if str(root).startswith(str(ws.resolve())):
                return _err("isolation_root must be outside the session workspace")
            iso = root / f"isolation_{time.strftime('%Y%m%d_%H%M%S')}"
            iso.mkdir(parents=True)
            shutil.copytree(pkg, iso / "pkg", symlinks=True)
            log = ws / "artifacts" / f"isolation_{iso.name}.log"
            env = {k: os.environ[k] for k in ("HOME", "USER", "SSH_AUTH_SOCK") if k in os.environ}
            env.update(TERM="dumb", OUTPUT_ROOT=str(iso / "out"), **(env_extra or {}))
            cmd = f"cd {iso} && bash pkg/{entry} {entry_args}; echo $? > {iso}/exit_code"
            with open(log, "w") as lf:
                p = subprocess.Popen(["env", "-i", *[f"{k}={v}" for k, v in env.items()], "bash", "-lc", cmd],
                                     stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
            state = {"pid": p.pid, "iso_dir": str(iso), "log": str(log), "entry": entry,
                     "entry_args": entry_args, "started": time.time(), "package": str(pkg)}
            state_p.write_text(json.dumps(state, indent=2))
            return _ok("isolation run launched", **state, next="poll with mode='status'; compare its result with the session's reference result")
        if mode == "status":
            if not state_p.exists():
                return _err("no isolation launch recorded for this session")
            s = json.loads(state_p.read_text())
            ec = Path(s["iso_dir"]) / "exit_code"
            lines = Path(s["log"]).read_text(errors="replace").splitlines() if Path(s["log"]).exists() else []
            done = ec.exists()
            return _ok("isolation run status", **s, finished=done, exit_code=int(ec.read_text().strip() or -1) if done else None,
                       elapsed_s=round(time.time() - s["started"]), tail="\n".join(lines[-tail_lines:]))
        return _err(f"unknown mode {mode} (static|launch|status)")

    @mcp.tool()
    def dftracer_isolation_check(run_id: str = "", package_dir: str = "final_report", workspace: str = "",
                                 loader: str = "lib_load_config.sh", entry: str = "scripts/run_all.sh",
                                 require_launch: bool = False) -> dict:
        """Validator for the isolation stage: rule deterministically on a reproduction package. Re-runs the static lint
        (no hardcoded workspace, home or user paths or flux job ids, every script sources the loader, required config.ini
        keys set, entry references exist, bash -n) and, when session_verify_isolation(mode='launch') was recorded, checks
        the isolated copy lives outside the session, finished, and exited 0. Args: run_id or workspace (path, overrides
        run_id), package_dir, loader, entry, require_launch (fail when no launch was recorded). Returns {status:
        pass|fail|inconclusive, checks: [{check, ok, detail, fix}], failed, workspace, package}."""
        if not run_id and not workspace:
            return _verdict([], error="give run_id or workspace")
        return _isolation_check(Path(workspace).expanduser() if workspace else _cap_ws(run_id), package_dir, loader, entry,
                                require_launch)

    @mcp.tool()
    def dftracer_isolation_selftest() -> dict:
        """Self-test of dftracer_isolation_check on a fake package in a temporary directory: a clean package with a
        finished launch that exited 0 passes; a script that does not source the loader fails static_clean; exit code 1
        fails exit_zero; a missing package fails. Returns {status: pass|fail, cases: [...]}."""
        cases = []
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d) / "ws"
            pkg = ws / "final_report"
            (pkg / "scripts").mkdir(parents=True)
            (ws / "artifacts").mkdir()
            (pkg / "config.ini").write_text("APP_ROOT=${HOME}/app\n")
            (pkg / "scripts" / "lib_load_config.sh").write_text("require() { :; }\nrequire APP_ROOT\n")
            (pkg / "scripts" / "run_all.sh").write_text("source scripts/lib_load_config.sh\necho run\n")
            iso = Path(d) / "iso" / "isolation_1"
            iso.mkdir(parents=True)
            (iso / "exit_code").write_text("0\n")
            (ws / "artifacts" / _ISO_STATE).write_text(json.dumps({"iso_dir": str(iso)}))
            cases.append({"case": "clean", "want": "pass", "got": _isolation_check(ws, "final_report", "lib_load_config.sh",
                                                                               "scripts/run_all.sh", True)["status"]})
            (iso / "exit_code").write_text("1\n")
            cases.append({"case": "exit1", "want": ["exit_zero"], "got": _isolation_check(
                ws, "final_report", "lib_load_config.sh", "scripts/run_all.sh", True)["failed"]})
            (pkg / "scripts" / "run_all.sh").write_text("echo run\n")
            cases.append({"case": "no_loader", "want": ["static_clean", "exit_zero"], "got": _isolation_check(
                ws, "final_report", "lib_load_config.sh", "scripts/run_all.sh", True)["failed"]})
            cases.append({"case": "no_pkg", "want": ["package"], "got": _isolation_check(
                ws, "nope", "lib_load_config.sh", "scripts/run_all.sh", True)["failed"]})
        ok = all(c["want"] == c["got"] for c in cases)
        return {"status": "pass" if ok else "fail", "cases": cases}
