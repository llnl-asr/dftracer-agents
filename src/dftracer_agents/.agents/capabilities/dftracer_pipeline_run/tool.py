"""dftracer_pipeline_run: run-ID management for dftracer pipelines. A run lives at
<workspaces root>/<app_name>/<YYYYMMDD_HHMMSS>/ (DFTRACER_WORKSPACES, default ./workspaces) with session.json, and
<workspaces root>/<app_name>/.current_run points at the active run.

Predictors: pipeline_create_run, pipeline_get_run_id, pipeline_list_runs (moved unchanged from dftracer-agents'
register_run_tools). Validator: dftracer_pipeline_run_check rules on their JSON results (status, run_id shape and
app prefix, workspace path, one current run, and optionally that the workspace, session.json and pointer exist).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from dftracer_agents.mcp_tools.tools.session.workspace import (
    _ws, _load_state, _ok, _err, _create_run, _workspaces_root, _derive_app_name,
)


def _chk(out: list, check: str, ok: bool, detail: str, fix: str = "") -> None:
    out.append({"check": check, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})


def check_run_result(result: Any, app: str = "", check_files: bool = False) -> Dict[str, Any]:
    out: List[dict] = []
    try:
        data = json.loads(result) if isinstance(result, str) else result
    except ValueError:
        data = None
    if not isinstance(data, dict):
        _chk(out, "result_json", False, "result is not a JSON object", "pass the tool's JSON result verbatim")
        return {"status": "fail", "checks": out}
    st = data.get("status")
    msg = str(data.get("message", ""))
    fix = ("no run exists for this app: call pipeline_create_run(app) first" if "No runs found" in msg else
           "no .current_run pointer: call pipeline_create_run, or pick one of available_runs" if "No active run" in msg
           else "read the message, fix the input and call again")
    _chk(out, "status", st == "ok", f"status={st!r}: {msg[:160]}", fix)
    if st != "ok":
        return {"status": "fail", "checks": out}
    app_name = data.get("app_name") or ""
    if app:
        want = _derive_app_name(app)
        _chk(out, "app_name", app_name == want, f"app_name {app_name!r}, derived from app {want!r}",
             "pass the same app value to every run tool (name, path or git URL all derive the same app_name)")
    if "runs" in data:                                   # pipeline_list_runs
        runs = data.get("runs") or []
        bad = [r.get("run_id") for r in runs if not str(r.get("run_id", "")).startswith(app_name + "/")]
        _chk(out, "runs_prefix", not bad, f"runs outside {app_name}/: {bad}" if bad else f"{len(runs)} run(s)",
             "a run directory belongs to one app: <app_name>/<run>")
        cur = [r.get("run_id") for r in runs if r.get("is_current")]
        cid = data.get("current_run_id")
        _chk(out, "one_current", len(cur) <= 1 and (cid is None or cid in [r.get("run_id") for r in runs] or not runs),
             f"current_run_id={cid!r}, marked current {cur}",
             "the .current_run pointer names a run that has no session.json: recreate it with pipeline_create_run "
             "or point it at an existing run")
        return {"status": "pass" if all(c["ok"] for c in out) else "fail", "checks": out}
    rid = str(data.get("run_id") or "")
    shape = re.match(r"^([a-z0-9_]+)/([^/\s]+)$", rid)
    _chk(out, "run_id_shape", bool(shape), f"run_id {rid!r}",
         "run_id must be <app_name>/<run>, e.g. ior/20261113_101500, as pipeline_create_run returns it")
    if shape:
        _chk(out, "run_id_app", shape.group(1) == app_name, f"run_id app {shape.group(1)!r} vs app_name {app_name!r}",
             "use the run_id returned for this app; never splice one together")
    ws = str(data.get("workspace") or "")
    _chk(out, "workspace_path", bool(ws) and ws.rstrip("/").endswith(rid), f"workspace {ws!r}",
         "the workspace is <workspaces root>/<run_id>; check DFTRACER_WORKSPACES is the same for every call")
    if check_files and ws:
        w = Path(ws)
        _chk(out, "workspace_exists", w.is_dir(), f"{w} exists: {w.is_dir()}",
             "the run directory is gone: create a new run with pipeline_create_run")
        _chk(out, "session_json", (w / "session.json").is_file(), "session.json present",
             "session.json is missing: the run was not created by pipeline_create_run; create a new run")
        ptr = w.parent / ".current_run"
        val = ptr.read_text().strip() if ptr.is_file() else None
        _chk(out, "current_pointer", val == rid, f".current_run = {val!r}",
             "another run became current after this one (pipeline_create_run moves the pointer): use "
             "pipeline_get_run_id to see the active run, or pass run_id explicitly")
    return {"status": "pass" if all(c["ok"] for c in out) else "fail", "checks": out}


def register(mcp):
    @mcp.tool()
    def pipeline_create_run(
        app: str,
        description: Optional[str] = None,
    ) -> str:
        """
        Create a deterministic run directory for a pipeline and remember the
        active run for the given application.

        The run ID is composed as ``<app_name>/<YYYYMMDD_HHMMSS>`` where
        ``app_name`` is derived from the ``app`` argument by extracting the
        basename, lower-casing it, and replacing non-alphanumeric characters
        with underscores.

        The workspace is created at::

            workspaces/<app_name>/<YYYYMMDD_HHMMSS>/

        A pointer file ``workspaces/<app_name>/.current_run`` is written so
        that ``pipeline_get_run_id`` can recall the active run without the
        caller having to track the ID themselves.

        Args:
            app:         Application name or path (e.g. ``ior``, ``/path/to/ior``,
                         ``https://github.com/org/myapp``).
            description: Optional free-text note stored in session.json.

        Returns:
            JSON with ``run_id``, ``app_name``, ``workspace``, ``created_at``.
        """
        run_id, ws = _create_run(app, description=description)
        state = _load_state(run_id)

        return _ok(
            f"Run {run_id} created",
            run_id=run_id,
            app_name=state.get("app_name", _derive_app_name(app)),
            workspace=str(ws),
            created_at=state.get("created_at", ""),
        )

    @mcp.tool()
    def pipeline_get_run_id(app: str) -> str:
        """
        Return the active run ID for the given application.

        Reads the pointer written by ``pipeline_create_run``.  If no run has
        been created yet for this application, lists the available run
        directories so the caller can pick one or call
        ``pipeline_create_run`` first.

        Args:
            app: Application name or path — same value passed to
                 ``pipeline_create_run``.

        Returns:
            JSON with ``run_id``, ``app_name``, ``workspace``, and
            ``created_at`` from the active run's session.json.
        """
        app_name = _derive_app_name(app)
        pointer = _workspaces_root() / app_name / ".current_run"

        if not pointer.exists():
            # Fall back: list available runs for this app so the caller can choose
            app_dir = _workspaces_root() / app_name
            if app_dir.is_dir():
                runs = sorted(
                    d.name for d in app_dir.iterdir()
                    if d.is_dir() and (d / "session.json").exists()
                )
            else:
                runs = []
            if runs:
                return _err(
                    f"No active run pointer for app '{app_name}'. "
                    f"Call pipeline_create_run first, or use one of the existing runs.",
                    app_name=app_name,
                    available_runs=[f"{app_name}/{r}" for r in runs],
                )
            return _err(
                f"No runs found for app '{app_name}'. "
                f"Call pipeline_create_run to start one.",
                app_name=app_name,
            )

        run_id = pointer.read_text().strip()
        ws = _ws(run_id)
        state = _load_state(run_id)
        return _ok(
            f"Active run for '{app_name}'",
            run_id=run_id,
            app_name=app_name,
            workspace=str(ws),
            created_at=state.get("created_at", "unknown"),
            step=state.get("step", "unknown"),
            description=state.get("description"),
        )

    @mcp.tool()
    def pipeline_list_runs(app: str) -> str:
        """
        List all run directories that exist for the given application.

        Args:
            app: Application name or path.

        Returns:
            JSON with ``app_name``, ``current_run_id``, and ``runs`` list
            (each entry has ``run_id``, ``created_at``, ``step``).
        """
        app_name = _derive_app_name(app)
        app_dir = _workspaces_root() / app_name

        pointer = app_dir / ".current_run"
        current_run_id = pointer.read_text().strip() if pointer.exists() else None

        if not app_dir.is_dir():
            return _ok(
                f"No runs found for app '{app_name}'",
                app_name=app_name,
                current_run_id=None,
                runs=[],
            )

        runs = []
        for d in sorted(app_dir.iterdir()):
            if not d.is_dir():
                continue
            sj = d / "session.json"
            if not sj.exists():
                continue
            state = json.loads(sj.read_text())
            runs.append({
                "run_id": f"{app_name}/{d.name}",
                "created_at": state.get("created_at", "unknown"),
                "step": state.get("step", "unknown"),
                "description": state.get("description"),
                "is_current": f"{app_name}/{d.name}" == current_run_id,
            })

        return _ok(
            f"{len(runs)} run(s) for app '{app_name}'",
            app_name=app_name,
            current_run_id=current_run_id,
            runs=runs,
        )

    @mcp.tool()
    def dftracer_pipeline_run_check(result: str, app: str = "", check_files: bool = False) -> dict:
        """Deterministic validator of the run-ID tools. Pass the JSON string returned by pipeline_create_run,
        pipeline_get_run_id or pipeline_list_runs as result. It checks status ok (an error gives the next action:
        create a run first, or pick one of available_runs), app_name equals the name derived from app (when given),
        run_id is '<app_name>/<run>' and the workspace path ends with it; for a list, every run is under the app and
        at most one is current and the current one exists. check_files=True also checks the workspace directory,
        its session.json and that <app>/.current_run names this run. Use it before handing a run_id to any stage.
        Returns {status: pass|fail, checks: [{check, ok, detail, fix}]}."""
        return check_run_result(result, app, check_files)
