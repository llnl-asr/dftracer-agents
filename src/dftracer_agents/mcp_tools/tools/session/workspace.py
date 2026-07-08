"""
Workspace helpers, subprocess runner, and response utilities for the dftracer
MCP session pipeline.

This module provides the foundational building blocks shared by every pipeline
stage (detect, build, install, run, …).  It owns three orthogonal concerns:

1. **Workspace layout** — every pipeline "run" gets an isolated directory tree
   rooted at ``$DFTRACER_WORKSPACES/<app_name>/<run_id>/``.  State is persisted
   as ``session.json`` inside that directory so that any MCP tool can resume a
   run across multiple calls.

2. **Subprocess execution** — a single :func:`_run` wrapper normalises stdout,
   stderr, return-code, and timeout handling into a uniform ``dict`` that every
   tool can pattern-match on.

3. **MCP response serialisation** — :func:`_ok` and :func:`_err` produce
   JSON-encoded response strings in the shape that MCP callers expect.

All public symbols are *private by convention* (``_``-prefixed) because they
are internal helpers imported by sibling modules, not exposed through the MCP
tool registry directly.  The ``DFTRACER_WORKSPACES`` environment variable
controls the root directory; it defaults to ``./workspaces`` relative to the
current working directory when the process starts.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------

#: Placeholder strings that an LLM might supply instead of a real run ID.
#: Any value in this set is treated as "no ID supplied" and triggers automatic
#: generation of a timestamp-based identifier.
_PLACEHOLDER_IDS = frozenset({
    "run_id", "RUN_ID", "RUN-ID", "<run_id>", "<RUN_ID>", "<RUN-ID>",
    "run-id", "runid", "RUNID", "{run_id}", "{RUN_ID}",
})


def _workspaces_root() -> Path:
    """Return the absolute path to the workspaces root directory.

    Reads the ``DFTRACER_WORKSPACES`` environment variable.  If the variable
    is unset, the literal string ``"workspaces"`` is used.  Relative values
    are resolved against the process current working directory at call time,
    not at import time, so the directory is always consistent with any
    ``os.chdir`` calls made after the module is loaded.

    Returns:
        Path: Absolute ``Path`` pointing to the workspaces root.  The
            directory is *not* created automatically; callers that need it to
            exist must call ``mkdir`` themselves.
    """
    env = os.environ.get("DFTRACER_WORKSPACES", "workspaces")
    root = Path(env)
    return root if root.is_absolute() else Path.cwd() / root


def _new_run_id(requested: Optional[str] = None) -> str:
    """Return a validated run ID, generating one when the supplied value is a placeholder.

    LLMs sometimes pass literal template strings such as ``"<run_id>"`` or
    ``"{RUN_ID}"`` instead of a real identifier.  This function detects those
    sentinel values (via :data:`_PLACEHOLDER_IDS`) and silently substitutes a
    UTC timestamp so the pipeline is never blocked on bad LLM output.

    Args:
        requested: The run ID supplied by the caller.  May be ``None``, an
            empty string, or one of the known placeholder strings.

    Returns:
        str: A non-empty run ID string.  Either *requested* (when it looks
            genuine) or a fresh ``YYYYMMDD_HHMMSS`` UTC timestamp string.
    """
    if requested and requested not in _PLACEHOLDER_IDS:
        return requested
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _create_run(
    app: str,
    run_id: Optional[str] = None,
    description: Optional[str] = None,
) -> tuple:
    """Create a structured run directory and record the initial pipeline state.

    The directory layout is::

        <workspaces_root>/<app_name>/<run_id>/
            session.json          ← persistent pipeline state
            artifacts/            ← per-step log files (created on demand)

    A ``<workspaces_root>/<app_name>/.current_run`` pointer file is also
    written so that ``pipeline_get_run_id`` can recall the most-recently
    created run for this application without the caller needing to track the
    ID manually.

    *run_id* is a **resume handle only**: it is honoured (namespaced under the
    app dir when bare) *only* when that session already exists on disk, letting
    callers resume an existing run without overwriting its ``session.json``. For
    a brand-new session a caller-supplied name is ignored and a fresh
    ``<app_name>/<YYYYMMDD_HHMMSS>`` id is always generated — the workspace
    layout is always ``<app_name>/<uid>``.

    Args:
        app: Application identifier.  Can be a bare name (``"ior"``), an
            absolute path (``"/path/to/ior"``), a Git HTTPS URL, or a Git SSH
            URL.  :func:`_derive_app_name` normalises this into a safe
            directory name.
        run_id: Desired run identifier.  When ``None`` or a known placeholder,
            a new ``<app_name>/YYYYMMDD_HHMMSS`` ID is generated.
        description: Optional human-readable description stored in
            ``session.json`` under the ``"description"`` key.

    Returns:
        tuple: A two-element tuple ``(run_id: str, workspace: Path)`` where
            *run_id* is the final identifier (generated or supplied) and
            *workspace* is the absolute ``Path`` to the run directory.
    """
    app_name = _derive_app_name(app)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if run_id and run_id not in _PLACEHOLDER_IDS:
        # `run_id` is a RESUME handle only. It must reference an existing
        # session on disk (bare ids are namespaced under the app dir); if it
        # does not exist we FAIL rather than silently creating a new run under
        # a caller-chosen name. New sessions always use <app_name>/<uid>.
        candidate = run_id if "/" in run_id else f"{app_name}/{run_id}"
        if not _ws(candidate).exists():
            raise FileNotFoundError(
                f"Cannot resume run '{run_id}': no session at {_ws(candidate)}. "
                f"Omit run_id to create a new '{app_name}/<timestamp>' session."
            )
        rid = candidate
    else:
        rid = f"{app_name}/{timestamp}"

    ws = _ws(rid)
    ws.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_state(rid, {
        "run_id": rid,
        "app_name": app_name,
        "app": app,
        "created_at": created_at,
        "workspace": str(ws),
        "step": "created",
        **({"description": description} if description else {}),
    })

    # Write pointer so pipeline_get_run_id can recall this run
    pointer = _workspaces_root() / app_name / ".current_run"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(rid)

    return rid, ws


def _derive_app_name(app: str) -> str:
    """Derive a safe, lowercase directory name from an app argument.

    The function strips URL/path structure and file extensions to extract a
    clean identifier that is safe for use as a filesystem directory name.
    Non-alphanumeric character runs are collapsed to a single underscore and
    the result is lower-cased.

    Handles the following input forms:

    - Bare names:           ``ior``              → ``ior``
    - Absolute paths:       ``/path/to/ior``     → ``ior``
    - Git HTTPS URLs:       ``https://github.com/org/ior.git`` → ``ior``
    - Git SSH URLs:         ``git@github.com:org/ior.git``     → ``ior``
    - Names with suffixes:  ``ior.exe``          → ``ior``

    Args:
        app: Raw application specifier as supplied by the caller.  May be
            any of the forms listed above.

    Returns:
        str: A non-empty, lowercase, filesystem-safe string.  Falls back to
            ``"app"`` if the derived name would otherwise be empty.
    """
    # Strip .git suffix common in URLs
    name = app.rstrip("/")
    if name.endswith(".git"):
        name = name[:-4]
    # Take the last path component (works for paths and URLs)
    name = Path(name.split(":")[-1]).name  # handles ssh git@host:org/repo
    # Strip file extensions
    name = name.split(".")[0] if "." in name else name
    # Lowercase and replace non-alphanumeric runs with a single underscore
    name = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return name or "app"


def _ws(run_id: str) -> Path:
    """Return the absolute path to the workspace directory for a given run.

    The directory is *not* guaranteed to exist; callers must create it when
    needed (e.g. via ``mkdir(parents=True, exist_ok=True)``).

    Args:
        run_id: The pipeline run identifier, typically of the form
            ``"<app_name>/YYYYMMDD_HHMMSS"``.

    Returns:
        Path: Absolute path ``<workspaces_root>/<run_id>``.
    """
    return _workspaces_root() / run_id


def _state_path(run_id: str) -> Path:
    """Return the absolute path to the ``session.json`` state file for a run.

    Args:
        run_id: The pipeline run identifier.

    Returns:
        Path: Absolute path ``<workspace>/session.json``.
    """
    return _ws(run_id) / "session.json"


def _load_state(run_id: str) -> Dict[str, Any]:
    """Load and return the persistent pipeline state for a run.

    If the ``session.json`` file does not yet exist (e.g. on the very first
    call before :func:`_create_run` has been called) an empty dict is returned
    rather than raising an exception.

    Args:
        run_id: The pipeline run identifier.

    Returns:
        Dict[str, Any]: Deserialised JSON state dict, or ``{}`` if the state
            file does not exist.
    """
    p = _state_path(run_id)
    return json.loads(p.read_text()) if p.exists() else {}


def _save_state(run_id: str, updates: Dict[str, Any]) -> None:
    """Merge *updates* into the persistent pipeline state and write it to disk.

    The function performs a **shallow merge**: existing keys are overwritten by
    values in *updates*; keys absent from *updates* are preserved.  Nested
    dicts are not deep-merged.

    The state file is written atomically in the sense that a complete JSON
    object is always written (not a partial append), so a crash mid-write
    produces an invalid JSON file rather than silently corrupt state.

    Args:
        run_id: The pipeline run identifier.
        updates: Key-value pairs to merge into the existing state.  The
            ``session.json`` file is created if it does not yet exist.
    """
    p = _state_path(run_id)
    state = _load_state(run_id)
    state.update(updates)
    p.write_text(json.dumps(state, indent=2))


def _safe_session_path(ws: Path, relpath: str) -> Path:
    """Resolve *relpath* under *ws*, raising if it would escape the session workspace.

    Guards against ``..``-traversal and absolute-path injection (``Path(base) /
    "/abs/path"`` silently resets to the absolute path in ``pathlib``) by
    resolving both sides and requiring the result to remain inside *ws*.

    Args:
        ws: Session workspace root (absolute ``Path``), typically ``_ws(run_id)``.
        relpath: Caller-supplied path, expected to be relative to *ws*.

    Returns:
        Path: The resolved absolute path, guaranteed to be inside *ws*.

    Raises:
        ValueError: If the resolved path is outside *ws*, or equals *ws* itself.
    """
    ws_resolved = ws.resolve()
    candidate = (ws / relpath).resolve()
    if not candidate.is_relative_to(ws_resolved):
        raise ValueError(f"path escapes session workspace: {relpath}")
    if candidate == ws_resolved:
        raise ValueError("refusing to operate on the session workspace root itself")
    return candidate


def _write_artifact_log(
    ws: Path,
    step_num: int,
    step_name: str,
    data: Dict[str, Any],
    run_id: str = "",
) -> Path:
    """Write a human-readable stage log file under ``<workspace>/artifacts/``.

    The file is named ``<NN>_<step_name>.log`` where ``NN`` is the zero-padded
    step number.  Each value in *data* is rendered as a flat ``key: value``
    line unless the value is itself a dict, in which case its ``stdout``,
    ``stderr``, and ``returncode``/``success`` sub-keys are extracted and
    indented for readability.

    The ``artifacts/`` directory is created automatically if it does not exist.

    Args:
        ws: Workspace root directory (absolute ``Path``).
        step_num: Zero-based ordinal of this pipeline step, used to order log
            files lexicographically.
        step_name: Short human-readable label for the step (e.g. ``"build"``),
            embedded in the filename and the log header.
        data: Mapping of named sections to log.  Dict values are treated as
            subprocess result records (see :func:`_run`); all other values are
            rendered with ``str()``.
        run_id: Optional run identifier included in the log header for
            traceability.  Defaults to the workspace directory name when empty.

    Returns:
        Path: Absolute path to the written log file.
    """
    artifacts = ws / "artifacts"
    artifacts.mkdir(exist_ok=True)
    base_name = f"{step_num:02d}_{step_name}.log"
    log_path = artifacts / base_name
    if log_path.exists():
        trial = 1
        while (artifacts / f"{base_name}.{trial}").exists():
            trial += 1
        log_path = artifacts / f"{base_name}.{trial}"
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"=== {step_name} ===",
        f"Timestamp : {ts}",
        f"Run ID    : {run_id or ws.name}",
        f"Step      : {step_num:02d}",
        "",
    ]
    for key, val in data.items():
        if isinstance(val, dict):
            lines.append(f"[{key}]")
            stdout = val.get("stdout", "")
            stderr = val.get("stderr", "")
            rc = val.get("returncode", val.get("success", ""))
            if stdout:
                lines.append(f"  stdout: {stdout}")
            if stderr:
                lines.append(f"  stderr: {stderr}")
            if rc != "":
                lines.append(f"  exit  : {rc}")
        else:
            lines.append(f"{key}: {val}")
    log_path.write_text("\n".join(lines) + "\n")
    return log_path


def _ok(msg: str, **extra) -> str:
    """Serialise a successful MCP tool response to a JSON string.

    The returned object always contains ``{"status": "ok", "message": msg}``
    plus any additional keyword arguments merged at the top level.

    Args:
        msg: Human-readable success message.
        **extra: Additional fields to include in the JSON response object.

    Returns:
        str: Pretty-printed (2-space indent) JSON string suitable for
            returning directly from an MCP tool handler.
    """
    return json.dumps({"status": "ok", "message": msg, **extra}, indent=2)


def _err(msg: str, **extra) -> str:
    """Serialise a failed MCP tool response to a JSON string.

    The returned object always contains ``{"status": "error", "message": msg}``
    plus any additional keyword arguments merged at the top level.

    Args:
        msg: Human-readable error description.
        **extra: Additional fields (e.g. ``run_id``, ``step``) to include in
            the JSON response object for diagnostic purposes.

    Returns:
        str: Pretty-printed (2-space indent) JSON string suitable for
            returning directly from an MCP tool handler.
    """
    return json.dumps({"status": "error", "message": msg, **extra}, indent=2)


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------

def _run(
    cmd: List[str],
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 600,
) -> Dict[str, Any]:
    """Execute a subprocess and return a normalised result dict.

    The current process environment is always inherited; *env* values are
    **merged on top** (not replaced), so callers only need to supply overrides.
    Both stdout and stderr are captured and stripped of leading/trailing
    whitespace.

    Timeout and unexpected exceptions are caught and surfaced as a failed
    result dict (``returncode == -1``) rather than propagating an exception,
    so callers can use uniform pattern-matching on the ``"success"`` key
    regardless of how the command terminated.

    Args:
        cmd: Command and arguments as a list of strings (passed directly to
            ``subprocess.run``; no shell interpretation).
        cwd: Working directory for the subprocess.  When ``None`` the current
            process working directory is inherited.
        env: Environment variable overrides.  Merged on top of
            ``os.environ``; keys present in *env* shadow the inherited value.
        timeout: Maximum number of seconds to wait for the process to finish.
            Defaults to 600 (10 minutes).  Processes that exceed this limit
            are killed and a ``"Command timed out"`` stderr is returned.

    Returns:
        Dict[str, Any]: A dict with the following keys:

            - ``returncode`` (int): Exit code, or ``-1`` on timeout/error.
            - ``stdout`` (str): Captured standard output, stripped of
              surrounding whitespace.
            - ``stderr`` (str): Captured standard error, stripped of
              surrounding whitespace.
            - ``success`` (bool): ``True`` iff ``returncode == 0``.
    """
    merged = {**os.environ, **(env or {})}
    try:
        r = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=merged,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "returncode": r.returncode,
            "stdout": r.stdout.strip(),
            "stderr": r.stderr.strip(),
            "success": r.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": "Command timed out", "success": False}
    except Exception as exc:
        return {"returncode": -1, "stdout": "", "stderr": str(exc), "success": False}
