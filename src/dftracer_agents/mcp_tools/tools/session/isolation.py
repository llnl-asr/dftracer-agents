"""Run-in-isolation verification for reproduction packages (final_report/, final_provenance/ ...).

A reproduction package must run from a fresh copy, from a clean login shell,
with only its ``config.ini`` filled in. ``session_verify_isolation`` checks that
deterministically:

* ``mode="static"``: no hardcoded workspace/home/user paths, usernames or flux
  job ids in scripts; every script sources the config loader; every script that
  ``run_all`` references exists; every ``require``-d key is set in config.ini;
  ``bash -n`` passes.
* ``mode="launch"``: copies the package into a NEW directory (never the session
  tree), and starts the entry script detached under ``env -i ... bash -l`` with
  ``OUTPUT_ROOT`` pointing inside that directory. Returns immediately.
* ``mode="status"``: exit code + log tail of a launched verification.

Run the static mode first and fix every finding; then launch; then poll status.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

from fastmcp import FastMCP

from .workspace import _ws, _ok, _err

STATE = "isolation_verify.json"
_JOBID = re.compile(r"\bf[1-9A-HJ-NP-Za-km-z]{10,12}\b")


def _static(pkg: Path, ws: Path, loader: str, entry: str) -> Dict[str, Any]:
    findings: List[str] = []
    scripts = sorted(pkg.glob("scripts/**/*.sh")) + sorted(pkg.glob("scripts/**/*.py"))
    home, user = os.path.expanduser("~"), os.environ.get("USER", "")
    forbidden = {str(ws.resolve()): "session workspace path", home: "home path"}
    if user:
        forbidden[f"/{user}/"] = "username in path"
    for f in scripts:
        text = f.read_text(errors="replace")
        rel = f.relative_to(pkg)
        for needle, what in forbidden.items():
            if needle and needle in text:
                findings.append(f"{rel}: hardcoded {what}")
        if _JOBID.search(text):
            findings.append(f"{rel}: looks like a hardcoded flux job id ({_JOBID.search(text).group(0)})")
        if f.suffix == ".sh":
            r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
            if r.returncode:
                findings.append(f"{rel}: bash -n: {r.stderr.strip()[:200]}")
            if f.name != loader and loader not in text:
                findings.append(f"{rel}: does not source {loader}")
    cfg = pkg / "config.ini"
    if not cfg.exists():
        findings.append("config.ini missing")
        keys = set()
    else:
        keys = {l.split("=", 1)[0].strip() for l in cfg.read_text().splitlines()
                if "=" in l and not l.strip().startswith("#")}
        if user and re.search(rf"(?<![\w$]){re.escape(user)}(?!\w)", cfg.read_text()):
            findings.append("config.ini: literal username (use ${USER})")
    lp = pkg / "scripts" / loader
    if lp.exists():
        for m in re.finditer(r"^\s*require\s+(.+)$", lp.read_text(), re.M):
            for k in m.group(1).split():
                if k not in keys:
                    findings.append(f"config.ini: required key {k} missing")
    else:
        findings.append(f"scripts/{loader} missing")
    ep = pkg / entry
    if not ep.exists():
        findings.append(f"{entry} missing")
    else:
        t = ep.read_text()
        for name in set(re.findall(r"(?<![$\w.-])([\w.-]+\.(?:sh|py))", t)):
            if name != ep.name and not list(pkg.glob(f"scripts/**/{name}")) and not (pkg / name).exists():
                findings.append(f"{entry}: references missing {name}")
        for loop in re.findall(r"for \w+ in ([^;]+);", t):
            for name in loop.split():
                if re.fullmatch(r"\d\d_[\w-]+", name) and not (pkg / "scripts" / f"{name}.sh").exists():
                    findings.append(f"{entry}: references missing {name}.sh")
    return {"scripts_checked": len(scripts), "findings": findings, "clean": not findings}


def register_isolation_tools(mcp: FastMCP) -> None:

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
