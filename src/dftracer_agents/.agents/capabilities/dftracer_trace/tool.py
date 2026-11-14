"""dftracer_trace: collect one run's traces under dftracer (per-run canonical paths, source snapshot + patch, the
run itself with DFTRACER_* set and the app's module preamble, optional allocation-aware flux wrapping), start/stop the
per-node dftracer_service daemon around it, wait for a flux job, summarise traces with dftracer_info, search papers and
the app's repo for a production configuration, and rule deterministically on the collected run (dftracer_trace_check).

Tools moved unchanged from dftracer-agents (session_tools.register_session_tools and
dftracer_service.register_daemon_tools).
"""
from __future__ import annotations

from pathlib import Path
import json
import shutil
import socket
from typing import Any, Dict, List, Optional

from dftracer_agents.mcp_tools.tools.session.config_search import (  # moved tools use these helpers unchanged
    search_papers_for_config,
)
from dftracer_agents.mcp_tools.tools.session.session_tools import (  # moved tools use these helpers unchanged
    _generate_patch,
    _run_dir,
    _run_patches_dir,
    _run_source_dir,
    _run_traces_compact,
    _run_traces_raw,
    _session_run_with_dftracer_impl,
    _snapshot_source,
)
from dftracer_agents.mcp_tools.tools.session.workspace import (  # moved tools use these helpers unchanged
    _err,
    _load_state,
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

_ATTEMPT = re.compile(r"^(?P<run>.+?)-(?P<attempt>[0-9a-f]{8})-")


def _add(checks, name, ok, detail, fix=""):
    checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})


def _verdict(checks, **extra):
    status = "inconclusive" if not checks else ("pass" if all(c["ok"] for c in checks) else "fail")
    return {"status": status, "checks": checks, "failed": [c["check"] for c in checks if not c["ok"]], **extra}


def _trace_files(d: Path) -> list:
    """Trace files by name and size only: this validator never opens a trace (TraceViewer / event_count do that)."""
    return sorted(list(d.rglob("*.pfw")) + list(d.rglob("*.pfw.gz"))) if d.is_dir() else []


def _trace_check(ws: Path, run_name: str, expected_ranks: int = 0, require_compact: bool = False) -> dict:
    checks: list = []
    if not ws.is_dir():
        _add(checks, "workspace", False, f"unknown run: no workspace at {ws}", "pass the run_id session_create returned")
        return _verdict(checks, workspace=str(ws))
    try:
        state = json.loads((ws / "session.json").read_text())
    except (OSError, ValueError):
        state = {}
    last = state.get(f"last_run_{run_name}")
    _add(checks, "run_recorded", isinstance(last, dict), f"last_run_{run_name} {'present' if isinstance(last, dict) else 'missing'}",
         f"run session_init_run(run_id, '{run_name}') then session_run_with_dftracer(run_id, command, run_name='{run_name}')")
    if isinstance(last, dict):
        _add(checks, "run_succeeded", bool(last.get("success")), f"returncode={last.get('returncode')}",
             "re-run the PRISTINE binary with the same arguments to attribute the failure before blaming the instrumentation; "
             "report the real status, never substitute a smaller run")
    raw = ws / run_name / "traces" / "raw"
    files = _trace_files(raw)
    app = [f for f in files if "service_" not in f.name]
    _add(checks, "traces_present", bool(app), f"{len(app)} app trace file(s) in {raw}",
         "no trace: check DFTRACER_ENABLE=1 reached every rank (wrapper script under flux proxy), DFTRACER_LOG_FILE's dir "
         "exists, DFTRACER_DATA_DIR=all, and the binary links dftracer (FUNCTION) or LD_PRELOAD is set (PRELOAD)")
    empty = [f.name for f in app if f.stat().st_size == 0]
    _add(checks, "no_zero_byte", not empty, f"flushed {len(app) - len(empty)}/{len(app)} ranks",
         "a zero-byte trace is a rank that never finalized (died at teardown, init without finalize): report the ratio, "
         "never round it up; check DATA_DIR/LOG_FILE before switching to PRELOAD")
    attempts = {m.group("attempt") for f in app if (m := _ATTEMPT.match(f.name))}
    _add(checks, "single_attempt", len(attempts) <= 1, f"attempt prefixes: {sorted(attempts)}",
         "traces of several attempts are mixed in one raw dir: remove the stale ones (session_run_with_dftracer clears raw/ "
         "itself; a hand-launched run does not)")
    if expected_ranks:
        _add(checks, "rank_count", len(app) >= expected_ranks, f"{len(app)} files for {expected_ranks} expected ranks",
             "fewer trace files than ranks: DFTRACER_* env not forwarded to every rank, or ranks died before init")
    if require_compact:
        comp = _trace_files(ws / run_name / "traces" / "compact")
        _add(checks, "compacted", bool(comp), f"{len(comp)} compact file(s)",
             "split before analysing: session_split_traces(run_id, run_name) (analysis of raw per-rank traces truncates silently)")
    if state.get("dftracer_service_state_dir"):
        _add(checks, "service_stopped", state.get("dftracer_service_running") is False,
             f"dftracer_service_running={state.get('dftracer_service_running')}",
             "call session_service_stop(run_id): the service trace only flushes on stop")
        svc = [f for f in _trace_files(ws / "traces") if f.name.startswith("service_")]
        svc_empty = [f.name for f in svc if f.stat().st_size == 0]
        _add(checks, "service_traces", bool(svc) and not svc_empty, f"{len(svc)} service trace(s), {len(svc_empty)} empty",
             "start, app and stop must land on the SAME hosts (pin with --requires=host:...); an empty service trace means "
             "the daemon on that node was never stopped")
    return _verdict(checks, workspace=str(ws), run_name=run_name, trace_files=len(app))


def register(mcp):
    """Register the dftracer_trace tools on *mcp*: the dftracer-agents tools moved here unchanged, then the validator."""

    @mcp.tool()
    def session_init_run(
        run_id: str,
        run_name: str,
    ) -> str:
        """Create the directory structure for a named profiling/optimization run.

        Every profiling iteration (baseline, opt1, opt2, …) lives under its own
        sub-directory inside the session workspace:

        .. code-block:: text

            <workspace>/
              <run_name>/
                traces/raw/      ← DFTRACER_LOG_FILE prefix goes here
                traces/compact/  ← session_split_traces output
                scripts/         ← launch scripts for this run
                source/          ← optional source snapshot

        Call this before ``session_run_with_dftracer`` to obtain the canonical
        paths for the run.  Calling it on an already-existing run is safe
        (directories are created only if absent).

        Args:
            run_id: Session identifier returned by ``session_create``.
            run_name: Short name for this run iteration, e.g. ``"baseline"``,
                ``"opt1"``, ``"opt2"``.  Used as the directory prefix.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"``).
                * ``message`` — confirmation.
                * ``run_name`` — the *run_name* argument.
                * ``run_dir`` — ``<workspace>/<run_name>/``.
                * ``traces_raw`` — ``<workspace>/<run_name>/traces/raw/``.
                * ``traces_compact`` — ``<workspace>/<run_name>/traces/compact/``.
                * ``scripts_dir`` — ``<workspace>/<run_name>/scripts/``.
                * ``dftracer_log_prefix`` — value to set for ``DFTRACER_LOG_FILE``.
        """
        ws = _ws(run_id)
        run_d = _run_dir(ws, run_name)
        raw = _run_traces_raw(ws, run_name)
        compact = _run_traces_compact(ws, run_name)
        source = _run_source_dir(ws, run_name)
        patches = _run_patches_dir(ws, run_name)
        scripts = run_d / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        log_prefix = str(raw / run_name)
        return _ok(
            f"Run directory initialised: {run_d}",
            run_name=run_name,
            run_dir=str(run_d),
            source_dir=str(source),
            patches_dir=str(patches),
            traces_raw=str(raw),
            traces_compact=str(compact),
            scripts_dir=str(scripts),
            dftracer_log_prefix=log_prefix,
        )

    @mcp.tool()
    def session_snapshot_run_source(
        run_id: str,
        run_name: str,
        source_path: Optional[str] = None,
        prev_run_name: Optional[str] = None,
    ) -> str:
        """Snapshot the current source tree into ``<run_name>/source/`` and generate a patch.

        Captures the state of the source code for a named run iteration so that
        every run has a reproducible record of exactly which code produced its
        traces.  Optionally generates a unified diff (patch) vs the previous run's
        snapshot.

        The canonical pipeline order is::

            baseline   → snapshot ws/source/   (no patch — this is the origin)
            annotated  → snapshot ws/annotated/ + patch vs baseline/source/
            opt1       → snapshot ws/annotated/ (after applying opt) + patch vs annotated/source/
            opt2       → snapshot ws/annotated/ (after applying opt2) + patch vs opt1/source/

        Side effects:
            * Copies *source_path* (or ``<workspace>/annotated/`` for annotated /
              opt runs, ``<workspace>/source/`` for baseline) into
              ``<workspace>/<run_name>/source/`` using rsync (shutil fallback).
            * If *prev_run_name* is given, writes a unified diff to
              ``<workspace>/<run_name>/patches/from_<prev_run_name>.patch``.
            * Persists ``{"snapshot_<run_name>": {...}}`` to ``session.json``.

        Args:
            run_id: Session identifier returned by ``session_create``.
            run_name: Run label (e.g. ``"baseline"``, ``"annotated"``, ``"opt1"``).
            source_path: Absolute path of the source tree to snapshot.
                Defaults to ``<workspace>/annotated/`` for non-baseline runs,
                or ``<workspace>/source/`` for ``"baseline"``.
            prev_run_name: If given, generates a patch from
                ``<workspace>/<prev_run_name>/source/`` to the new snapshot.

        Raises:
            None directly — if *source_path* is the same as, or nested inside
            (or an ancestor of), the resolved destination
            ``<workspace>/<run_name>/source/``, the tool returns an error
            result instead of copying. Overlapping paths would make the copy
            read from its own destination, corrupting the tree it's writing.
            Pick a *source_path* that lives outside the destination run's own
            directory (e.g. don't snapshot ``annotated`` into itself by
            passing ``source_path="<WS>/annotated/source"`` when
            ``run_name="annotated"`` — omit *source_path* and let the default
            resolution handle it, or point at a different run's source).

        Returns:
            JSON with ``source_dir``, ``file_count``, and (if *prev_run_name*)
            ``patch_file``.
        """
        ws = _ws(run_id)

        # Resolve default source path
        if source_path:
            src = Path(source_path)
        elif run_name == "baseline":
            src = ws / "source"
        else:
            src = ws / "annotated"
            if not src.exists():
                src = ws / "source"

        if not src.exists():
            return _err(f"Source path not found: {src}")

        dest = _run_source_dir(ws, run_name)
        try:
            snap = _snapshot_source(src, dest)
        except ValueError as e:
            return _err(str(e), source=str(src), dest=str(dest))
        if not snap["success"]:
            return _err(f"Snapshot failed for {run_name}", source=str(src))

        result: Dict[str, Any] = {
            "source_dir": str(dest),
            "source_from": str(src),
            "file_count": snap["file_count"],
            "tool": snap.get("tool"),
        }

        # Generate patch vs previous run
        if prev_run_name:
            prev_source = ws / prev_run_name / "source"
            if prev_source.exists():
                patch_file = _run_patches_dir(ws, run_name) / f"from_{prev_run_name}.patch"
                patch_status = _generate_patch(prev_source, dest, patch_file)
                result["patch_file"] = str(patch_file)
                result["patch_status"] = patch_status
            else:
                result["patch_file"] = None
                result["patch_status"] = f"skipped: {prev_run_name}/source/ not found"

        _save_state(run_id, {f"snapshot_{run_name}": result})
        return _ok(f"Snapshotted {src.name} → {run_name}/source/ ({snap['file_count']} files)", **result)

    @mcp.tool()
    def session_run_with_dftracer(
        run_id: str,
        command: str,
        subfolder: str = "build_ann",
        data_dir: str = "all",
        timeout: int = 600,
        env_extra: Optional[str] = None,
        run_name: str = "baseline",
        allocation_id: Optional[str] = None,
        nnodes: Optional[int] = None,
        ntasks: Optional[int] = None,
    ) -> str:
        """Run a command with dftracer environment variables set to capture traces.

        Executes *command* inside the workspace with all ``DFTRACER_*`` env
        vars configured.  Trace files are written to
        ``<workspace>/<run_name>/traces/raw/`` and consumed by
        ``session_split_traces``.

        The following variables are always set:

        * ``DFTRACER_ENABLE=1``         — activates tracing.
        * ``DFTRACER_INC_METADATA=1``   — records process/thread metadata.
        * ``DFTRACER_LOG_FILE=<workspace>/<run_name>/traces/raw/<run_name>``
          — trace file prefix; dftracer appends ``.<pid>.pfw``.
        * ``DFTRACER_DATA_DIR=all``     — captures I/O on *any* file path.
          Pass an explicit path via *data_dir* only to restrict monitoring
          to a subtree.

        ``DFTRACER_INIT`` is intentionally **not** set here.  Pass it via
        *env_extra* only when the annotated source has no explicit
        ``DFTRACER_C_INIT`` / ``DFTRACER_CPP_INIT`` calls.

        Additional variables can be merged/overridden via *env_extra*.

        **Allocation-aware runs:** For production-scale runs, provide *allocation_id*,
        *nnodes*, and *ntasks* to wrap the command with ``flux proxy <alloc> flux run``
        using all nodes in the allocation. The tracer agent must verify the allocation
        is active before running.

        Side effects:
            * Creates ``<workspace>/<run_name>/traces/raw/`` if absent.
            * Writes trace files inside that directory.
            * Persists ``{"step": "ran_with_dftracer", ...}`` to ``session.json``.
            * Writes an artifact log at step 11.

        Args:
            run_id: Session identifier returned by ``session_create``.
            command: Shell command to run (via ``/bin/sh -c``).
            subfolder: Working-directory sub-folder relative to the workspace
                root.  Defaults to ``"build_ann"``.  Falls back to ``"build"``
                then ``"source"`` if absent.
            data_dir: Value passed to ``DFTRACER_DATA_DIR``.  Defaults to
                ``"all"``.
            timeout: Seconds before the subprocess is killed (default: 600).
            env_extra: Optional JSON object string of additional env vars.
            run_name: Named run label — e.g. ``"baseline"``, ``"opt1"``.
                Traces are stored under ``<workspace>/<run_name>/traces/raw/``.
                Defaults to ``"baseline"``.
            allocation_id: Optional Flux allocation ID (e.g. ``"<flux-jobid>"``).
                When provided, the command is wrapped with
                ``flux proxy <alloc_id> flux run -N <nnodes> -n <ntasks> --exclusive``.
            nnodes: Number of nodes to use (required when allocation_id is set).
            ntasks: Number of MPI tasks (required when allocation_id is set).

        Returns:
            JSON string with keys ``status``, ``message``, ``run_name``,
            ``traces_dir``, ``stdout``, ``stderr``, ``returncode``.

        Note:
            Must be called after ``session_build_annotated``.  Follow with
            ``session_split_traces`` (same *run_name*) to compact the raw files.
        """
        return _session_run_with_dftracer_impl(
            run_id=run_id,
            command=command,
            subfolder=subfolder,
            data_dir=data_dir,
            timeout=timeout,
            env_extra=env_extra,
            run_name=run_name,
            allocation_id=allocation_id,
            nnodes=nnodes,
            ntasks=ntasks,
        )

    @mcp.tool()
    def session_wait_flux_job(
        alloc_id: str,
        job_id: str,
        poll_interval: int = 30,
        timeout: int = 7200,
        log_file: Optional[str] = None,
    ) -> str:
        """Wait for a Flux job (inside an allocation) to complete, polling until done.

        Uses ``flux proxy <alloc_id> flux jobs -a`` to poll the job state every
        *poll_interval* seconds until the job reaches a terminal state (CD, CA, F)
        or *timeout* seconds elapse.

        After the job finishes, optionally tails the last 50 lines of *log_file*
        (if provided) so the caller can see the final output without reading the
        whole file.

        Args:
            alloc_id: The allocation JOBID to proxy into (e.g. ``"<flux-jobid>"``).
            job_id: The inner job JOBID to wait for (e.g. ``"f6H3GwWJpo"``).
            poll_interval: Seconds between status polls. Defaults to ``30``.
            timeout: Maximum seconds to wait. Defaults to ``7200`` (2 hours).
            log_file: Optional absolute path to a log file whose last 50 lines
                are returned in the response after the job finishes.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"``, ``"error"``, or ``"timeout"``).
                * ``message`` — human-readable summary.
                * ``job_state`` — final flux job state (``"CD"``, ``"CA"``, ``"F"``).
                * ``elapsed`` — seconds waited.
                * ``log_tail`` — last 50 lines of *log_file* (if provided and exists).
        """
        import time as _time
        import subprocess as _sp

        start = _time.time()
        terminal = {"CD", "CA", "F"}
        job_state = None
        elapsed = 0

        while elapsed < timeout:
            try:
                result = _sp.run(
                    ["flux", "proxy", alloc_id, "flux", "jobs", "-a"],
                    capture_output=True, text=True, timeout=30,
                )
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if parts and parts[0] == job_id:
                        job_state = parts[3] if len(parts) > 3 else None
                        break
            except Exception:
                pass

            if job_state in terminal:
                break

            _time.sleep(poll_interval)
            elapsed = int(_time.time() - start)

        elapsed = int(_time.time() - start)

        log_tail = ""
        if log_file:
            try:
                with open(log_file, "r") as fh:
                    lines = fh.readlines()
                    log_tail = "".join(lines[-50:])
            except Exception:
                log_tail = "(could not read log file)"

        if elapsed >= timeout and job_state not in terminal:
            return json.dumps({
                "status": "timeout",
                "message": f"Job {job_id} did not finish within {timeout}s",
                "job_state": job_state,
                "elapsed": elapsed,
                "log_tail": log_tail,
            })

        ok = job_state == "CD"
        return json.dumps({
            "status": "ok" if ok else "error",
            "message": f"Job {job_id} finished with state {job_state} after {elapsed}s",
            "job_state": job_state,
            "elapsed": elapsed,
            "log_tail": log_tail,
        })

    @mcp.tool()
    def session_analyze_traces(
        run_id: str,
        run_name: str = "baseline",
        query_type: str = "summary",
        index_dir: Optional[str] = None,
        extra_flags: str = "",
        subpath: str = "",
    ) -> str:
        """Summarise dftracer traces using ``dftracer_info`` (dfanalyzer).

        Invokes ``dftracer_info`` against the compacted trace directory
        (``<workspace>/<run_name>/traces/compact/``) to produce a summary of
        I/O behaviour — function call counts, time in I/O, per-file breakdowns,
        and process metadata.  The index directory caches parsed data for faster
        repeated queries.

        Side effects:
            * Creates *index_dir* (or ``<compact>/idx/``) if absent.
            * Persists ``{"step": "traces_analyzed_<run_name>", ...}`` to
              ``session.json``.
            * Writes an artifact log at step 13.

        Args:
            run_id: Session identifier returned by ``session_create``.
            run_name: Run label whose compact traces to analyse (default
                ``"baseline"``).  Maps to
                ``<workspace>/<run_name>/traces/compact/``.
            query_type: Value passed to ``dftracer_info --query``.  Common
                values: ``"summary"`` (default), ``"function"``, ``"file"``.
            index_dir: Absolute path to the index directory.  Defaults to
                ``<compact>/idx/``.
            extra_flags: Additional space-separated flags for ``dftracer_info``.
            (No timeout is applied to the ``dftracer_info`` subprocess — large
            (26M+ event) trace sets can legitimately take much longer to
            analyze than smaller ones, and a fixed bound would kill a
            legitimate long-running analysis.)
            subpath: Optional relative path appended under ``<compact>/`` before
                scanning, e.g. ``"write/rep1"`` for sessions whose split traces
                are folder-segregated by workload/replicate
                (``<compact>/<workload>/<rep>/``) rather than flat.
                ``dftracer_info -d`` is non-recursive, so without this the tool
                reports "No files found" against a compact root that only
                contains subdirectories. Leave empty for the flat/default layout.

        Returns:
            JSON string with keys ``status``, ``message``, ``run_name``,
            ``stdout``, ``stderr``, ``returncode``.

        Raises:
            Returns ``{"status": "error"}`` when the compact directory does not
            exist or when ``dftracer_info`` exits non-zero.

        Note:
            Must be called after ``session_split_traces`` with the same
            *run_name*.
        """
        ws = _ws(run_id)
        traces = ws / run_name / "traces" / "compact"
        if not traces.exists():
            # Legacy fallback for sessions predating the structured layout
            legacy = ws / "traces_split"
            if legacy.exists():
                traces = legacy
            else:
                return _err(f"{run_name}/traces/compact/ not found — run session_split_traces first")

        if subpath:
            candidate = traces / subpath
            if not candidate.exists():
                return _err(f"{run_name}/traces/compact/{subpath} not found")
            traces = candidate

        idx = Path(index_dir) if index_dir else traces / "idx"
        idx.mkdir(parents=True, exist_ok=True)

        # dftracer_info --query accepts ONLY "summary" or "detailed". It silently
        # ignores anything else and returns the summary, which made callers passing
        # query_type="function"/"file" believe the parameter was unwired.
        _VALID_QUERIES = {"summary", "detailed"}
        if query_type not in _VALID_QUERIES:
            return _err(
                f"invalid query_type {query_type!r}; dftracer_info supports only "
                f"{sorted(_VALID_QUERIES)}. Passing anything else silently returns "
                f"the summary."
            )

        flags = extra_flags.split() if extra_flags else []
        r = _run(
            [
                "dftracer_info",
                "-d", str(traces),
                "--query", query_type,
                "--index-dir", str(idx),
            ] + flags,
            # No timeout: analysis of large (26M+ event) trace sets can
            # legitimately run far longer than a fixed bound; killing it
            # early just produces a spurious failure instead of a result.
            timeout=None,
        )
        _save_state(run_id, {f"step_analyzed_{run_name}": True, "analysis_result": r})
        _write_artifact_log(_ws(run_id), 13, "session_analyze_traces", {"run_name": run_name, **r}, run_id)
        if r["success"]:
            return _ok("Analysis complete", run_name=run_name, **r)
        return _err("dftracer_info failed", run_name=run_name, **r)

    @mcp.tool()
    def session_search_papers_for_config(
        run_id: str,
        app_name: str,
        problem_name: Optional[str] = None,
        max_results_each: int = 3,
    ) -> str:
        """Search academic literature AND GitHub repos for application-specific run configuration.

        Before executing a production-scale run, agents MUST call this tool to
        discover benchmark-proven parameter values (grid size, checkpoint
        intervals, refinement levels, etc.) from published papers AND the app's
        official GitHub repository.  The results are persisted to ``session.json``
        so downstream steps (planner, tracer, optimizer) can read them without
        re-searching.

        Workflow:
          1. Build targeted queries from *app_name* + *problem_name*.
          2. Search arXiv + Semantic Scholar in parallel.
          3. Search the app's official GitHub repository for benchmark parameter files.
          4. Extract known parameter patterns from paper titles/abstracts AND repo files.
          5. Persist findings and return structured recommendations.

        Side effects:
            * Writes ``<workspace>/artifacts/paper_config_search.json``.
            * Persists ``{"paper_config_search": {...}}`` to ``session.json``.

        Args:
            run_id: Session identifier returned by ``session_create``.
            app_name: Application name (e.g. ``"Flash-X"``, ``"IOR"``, ``"h5bench"``).
            problem_name: Optional problem / benchmark name (e.g. ``"Sedov"``,
                ``"weak"``, ``"strong"``).
            max_results_each: Papers to fetch per source (default 3).

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"``).
                * ``message`` — human-readable summary.
                * ``queries`` — list of search strings used.
                * ``paper_count`` — total unique papers found.
                * ``papers`` — list of paper dicts (title, authors, year, url, pdf_url).
                * ``github_files`` — list of benchmark parameter files found in the app's repo.
                * ``github_params`` — dict of parameters extracted from repo files.
                * ``extracted_params`` — combined dict of all parameters found (papers + GitHub).
                * ``recommendations`` — human-readable bullet list.
        """
        ws = _ws(run_id)
        if not ws.exists():
            return _err(f"Session {run_id} not found")

        result = search_papers_for_config(
            app_name=app_name,
            problem_name=problem_name,
            max_results_each=max_results_each,
        )

        # Persist to artifact file
        artifact = ws / "artifacts" / "paper_config_search.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps(result, indent=2, default=str))

        _save_state(run_id, {"paper_config_search": result})
        _write_artifact_log(
            ws, 15, "session_search_papers_for_config",
            {"app_name": app_name, "problem_name": problem_name, **result},
            run_id,
        )
        total_sources = result.get("paper_count", 0) + result.get("github_file_count", 0)
        return _ok(
            f"Found {result.get('paper_count', 0)} papers and {result.get('github_file_count', 0)} repo files for {app_name} (total sources: {total_sources})",
            **result,
        )

    @mcp.tool()
    def session_service_start(
        run_id: str,
        trace_interval_ms: int = 1000,
        libuv_threads: int = 1,
    ) -> str:
        """Start the ``dftracer_service`` background daemon for this session.

        Locates the ``dftracer_service`` binary (first in the session's
        ``install_ann/bin/``, then via ``PATH``), then starts it as an
        independent per-node daemon with the appropriate ``DFTRACER_*``
        environment variables.

        The daemon writes its own trace files under a prefix separate from the
        application traces produced by ``session_run_with_dftracer``:

        * **Service traces** → ``<workspace>/traces/service_<hostname>.*``
        * **App traces**     → ``<workspace>/traces/<run_id>.*``

        Both trace sets land in the same ``traces/`` directory and are picked up
        by ``session_split_traces``.

        The daemon state directory (``<workspace>/traces/dftracer_service/<hostname>/``)
        is persisted to ``session.json`` so that ``session_service_stop`` can
        locate it without additional arguments.

        Args:
            run_id: Session identifier returned by ``session_create``.
            trace_interval_ms: Polling interval in milliseconds passed to
                ``DFTRACER_TRACE_INTERVAL_MS``.  Defaults to ``1000``.
            libuv_threads: Number of libuv I/O threads passed to
                ``DFTRACER_LIBUV_THREADS``.  Defaults to ``1``.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"`` or ``"error"``).
                * ``message`` — outcome description including the hostname.
                * ``state_dir`` — absolute path to the daemon state directory.
                * ``log_prefix`` — ``DFTRACER_LOG_FILE`` prefix used by the daemon.
                * ``stdout``, ``stderr``, ``returncode`` — from the ``start`` invocation.

        Raises:
            Returns ``{"status": "error"}`` when:
                * The binary is not found in ``install_ann/bin/`` or ``PATH``.
                * ``dftracer_service start`` exits non-zero.
        """
        ws = _ws(run_id)
        traces_dir = ws / "traces"
        traces_dir.mkdir(exist_ok=True)

        service_bin = ws / "install_ann" / "bin" / "dftracer_service"
        if not service_bin.exists():
            found = shutil.which("dftracer_service")
            if found:
                service_bin = Path(found)
            else:
                return _err(
                    "dftracer_service binary not found — run session_install_dftracer first",
                    searched=[
                        str(ws / "install_ann" / "bin" / "dftracer_service"),
                        "PATH",
                    ],
                )

        node_name = socket.gethostname().split(".")[0]
        state_dir = traces_dir / "dftracer_service" / node_name
        state_dir.mkdir(parents=True, exist_ok=True)
        log_prefix = str(traces_dir / f"service_{node_name}")

        env = {
            "DFTRACER_ENABLE": "1",
            "DFTRACER_LOG_FILE": log_prefix,
            "DFTRACER_TRACE_INTERVAL_MS": str(trace_interval_ms),
            "DFTRACER_LIBUV_THREADS": str(libuv_threads),
        }

        r = _run([str(service_bin), "start", str(state_dir)], env=env, timeout=30)

        _save_state(run_id, {
            "dftracer_service_state_dir": str(state_dir),
            "dftracer_service_log_prefix": log_prefix,
            "dftracer_service_bin": str(service_bin),
            "dftracer_service_running": r["success"],
        })

        if r["success"]:
            return _ok(
                f"dftracer_service started on {node_name}",
                state_dir=str(state_dir),
                log_prefix=log_prefix,
                **r,
            )
        return _err(
            f"dftracer_service start failed on {node_name}",
            state_dir=str(state_dir),
            **r,
        )

    @mcp.tool()
    def session_service_stop(run_id: str) -> str:
        """Stop the ``dftracer_service`` background daemon for this session.

        Reads the daemon state directory from ``session.json`` (written by
        ``session_service_start``) and runs ``dftracer_service stop <state_dir>``.

        A non-zero exit from ``stop`` is treated as a *warning*, not an error,
        because the daemon may have already exited cleanly.  The tool always
        returns ``status: "ok"`` so the pipeline can continue regardless.

        Args:
            run_id: Session identifier returned by ``session_create``.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"`` or ``"error"``).
                * ``message`` — outcome description.
                * ``state_dir`` — the daemon state directory that was used.
                * ``stdout``, ``stderr``, ``returncode`` — from the ``stop`` invocation.

        Raises:
            Returns ``{"status": "error"}`` when:
                * ``session_service_start`` was never called (no state in ``session.json``).
                * The binary cannot be located at all (neither saved path nor ``PATH``).
        """
        ws = _ws(run_id)
        state = _load_state(run_id)

        state_dir_str = state.get("dftracer_service_state_dir")
        if not state_dir_str:
            return _err(
                "dftracer_service state not found — was session_service_start called?",
                run_id=run_id,
            )

        state_dir = Path(state_dir_str)

        bin_str = state.get("dftracer_service_bin", "")
        service_bin = Path(bin_str) if bin_str and Path(bin_str).exists() else None
        if service_bin is None:
            candidate = ws / "install_ann" / "bin" / "dftracer_service"
            if candidate.exists():
                service_bin = candidate
            else:
                found = shutil.which("dftracer_service")
                if found:
                    service_bin = Path(found)
                else:
                    return _err(
                        "dftracer_service binary not found — cannot stop daemon",
                        state_dir=state_dir_str,
                    )

        r = _run([str(service_bin), "stop", str(state_dir)], timeout=30)
        _save_state(run_id, {"dftracer_service_running": False})

        if r["success"]:
            return _ok("dftracer_service stopped", state_dir=state_dir_str, **r)
        # Non-zero stop is non-fatal; daemon may have already exited cleanly
        return _ok(
            "dftracer_service stop returned non-zero (daemon may have already exited)",
            state_dir=state_dir_str,
            **r,
        )

    @mcp.tool()
    def dftracer_trace_check(run_id: str = "", run_name: str = "baseline", workspace: str = "", expected_ranks: int = 0,
                             require_compact: bool = False) -> dict:
        """Validator for the trace stage: rule deterministically on one collected run from file names, sizes and
        session.json (it never opens a trace). Checks session_run_with_dftracer recorded the run and it succeeded, app
        traces exist in <ws>/<run_name>/traces/raw, none is zero-byte (reports flushed/total ranks), they come from a single
        attempt, optionally there are at least expected_ranks files and a compacted copy exists, and, when the
        dftracer_service daemon was started, it was stopped and left non-empty service traces. Completeness (the trailing
        'end' event) and event counts are for event_count / the reader. Args: run_id or workspace (path, overrides
        run_id), run_name, expected_ranks, require_compact. Returns {status: pass|fail|inconclusive, checks: [{check, ok,
        detail, fix}], failed, workspace, run_name, trace_files}."""
        if not run_id and not workspace:
            return _verdict([], error="give run_id or workspace")
        return _trace_check(Path(workspace).expanduser() if workspace else _cap_ws(run_id), run_name, expected_ranks,
                            require_compact)

    @mcp.tool()
    def dftracer_trace_selftest() -> dict:
        """Self-test of dftracer_trace_check on fixtures in a temporary directory (fake trace files, never real traces):
        a 2-rank run with a stopped daemon passes; a zero-byte rank fails no_zero_byte; a second attempt fails
        single_attempt; a running daemon fails service_stopped; expected_ranks=4 fails rank_count. Returns {status, cases}."""
        cases = []
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d) / "app" / "20260101_000000"
            raw = ws / "baseline" / "traces" / "raw"
            raw.mkdir(parents=True)
            (ws / "traces").mkdir()
            for i in (1, 2):
                (raw / f"baseline-0a1b2c3d-h{i}-app.pfw.gz").write_text("x")
            (ws / "traces" / "service_node1-x.pfw.gz").write_text("x")
            st = {"last_run_baseline": {"success": True, "returncode": 0}, "dftracer_service_state_dir": "s",
                  "dftracer_service_running": False}
            (ws / "session.json").write_text(json.dumps(st))
            cases.append({"case": "good", "want": "pass", "got": _trace_check(ws, "baseline")["status"]})
            cases.append({"case": "ranks", "want": ["rank_count"], "got": _trace_check(ws, "baseline", 4)["failed"]})
            (raw / "baseline-0a1b2c3d-h3-app.pfw.gz").write_text("")
            cases.append({"case": "zero", "want": ["no_zero_byte"], "got": _trace_check(ws, "baseline")["failed"]})
            (raw / "baseline-0a1b2c3d-h3-app.pfw.gz").unlink()
            (raw / "baseline-ffffffff-h1-app.pfw.gz").write_text("x")
            cases.append({"case": "mixed", "want": ["single_attempt"], "got": _trace_check(ws, "baseline")["failed"]})
            (raw / "baseline-ffffffff-h1-app.pfw.gz").unlink()
            st["dftracer_service_running"] = True
            (ws / "session.json").write_text(json.dumps(st))
            cases.append({"case": "daemon", "want": ["service_stopped"], "got": _trace_check(ws, "baseline")["failed"]})
        ok = all(c["want"] == c["got"] for c in cases)
        return {"status": "pass" if ok else "fail", "cases": cases}
