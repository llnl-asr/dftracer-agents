"""dftracer_analyze: run dfanalyzer and the dftracer-utils trace tools (reader, info, event_count, split, stats, aggregator, comparator, view, index, MPI variants) over a run's compact traces, and validate what the analysis used and produced (dftracer_analyze_check)."""
from __future__ import annotations

import subprocess
from dftracer_agents.mcp_tools.tools.dftracer._flux_exec import run_on_allocation
from dftracer_agents.mcp_tools.tools.dftracer.dfanalyzer_service import _ensure_analyzable_path
from dftracer_agents.mcp_tools.tools.dftracer.dfanalyzer_service import _hydra_args
from typing import List
from typing import Optional

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from dftracer_agents.mcp_tools.tools.optimizations.strategies import _BUILTIN_REFS

# --------------------------------------------------------------------------- validator helpers (deterministic, offline)
_LEVELS = ("L1", "L2", "L3")
_SCOPE_LEVEL = {"workload": "L1", "software": "L2", "system": "L3"}
_ARXIV_RX = re.compile(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b")
_DOI_RX = re.compile(r"\b10\.\d{4,9}/\S+")
_URL_RX = re.compile(r"https?://[^\s/]+\.[^\s]+")
_YEAR_RX = re.compile(r"\b(?:19|20)\d{2}\b")
# "Doing less" is not optimizing: fewer epochs/steps, a smaller input, skipped checkpoints or output.
_DO_LESS_RX = re.compile(
    r"\b(?:reduce|reducing|fewer|cut|cutting|skip|skipping|drop|dropping|disable|disabling|remove|removing|shrink|smaller|lower|lowering)\b"
    r"[^.;\n]{0,40}?\b(?:epochs?|iterations?|steps?|timesteps?|dataset|samples|problem size|input size|checkpoints?|"
    r"output files?|outputs?|validation|tolerance|resolution)\b", re.I)


def _add(checks: List[dict], name: str, ok: bool, detail: str, fix: str = "") -> None:
    checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})


def _verdict(checks: List[dict], **extra) -> str:
    if not checks:
        return json.dumps({"status": "inconclusive", "checks": [], "reason": "nothing to check: give at least one input", **extra})
    failed = [c["check"] for c in checks if not c["ok"]]
    return json.dumps({"status": "fail" if failed else "pass", "checks": checks, "failed": failed, **extra}, default=str)


def _parse_json_arg(text: str):
    """JSON text, or the path of a JSON file. Raises ValueError with a readable message."""
    t = (text or "").strip()
    if not t:
        raise ValueError("empty input")
    if t[0] not in "[{\"" and Path(t).is_file():
        t = Path(t).read_text()
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON: {e}") from None


def _rows(data, keys=("rows", "proposals", "accepted", "findings", "papers", "citations", "entries")) -> List[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return data[k]
    return []


def _citation_problem(cit) -> str:
    """'' when the citation is usable, else why it is not."""
    if isinstance(cit, dict):
        if not str(cit.get("title") or "").strip():
            return "citation object has no title"
        if not (cit.get("url") or cit.get("doi") or (cit.get("venue") and cit.get("year"))):
            return "citation object needs url, doi, or venue and year"
        return ""
    c = str(cit or "").strip()
    if not c:
        return "no citation"
    if c.lower().startswith("session:"):
        return "" if len(c) > len("session:") else "session: citation without a run id"
    if c.lower() in _BUILTIN_REFS:
        return ""
    if _URL_RX.search(c) or _DOI_RX.search(c) or _ARXIV_RX.search(c):
        return ""
    if _YEAR_RX.search(c) and len(c) >= 15:
        return ""
    return "not a URL, DOI, arXiv id, built-in reference key or 'Author et al., Venue Year'"


def _level_of(row: dict) -> str:
    lv = str(row.get("level") or row.get("scope") or "").strip()
    if lv.upper() in _LEVELS:
        return lv.upper()
    return _SCOPE_LEVEL.get(lv.lower(), "")


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _row_text(row: dict) -> str:
    return " ".join(str(row.get(k) or "") for k in ("title", "change", "strategy", "description"))

# --------------------------------------------------------------------------- dftracer_analyze_check
_TRACE_GLOBS = ("*.pfw.gz", "*.pfw")


def _trace_files(d: Path) -> List[Path]:
    out: List[Path] = []
    for g in _TRACE_GLOBS:
        out += [p for p in d.rglob(g) if p.is_file()]
    return sorted(set(out))


def _check_trace_dir(checks: List[dict], label: str, path: str) -> List[Path]:
    d = Path(path)
    _add(checks, f"{label}_exists", d.is_dir(), str(d), f"give the compact trace directory of the run ({label})")
    if not d.is_dir():
        return []
    files = _trace_files(d)
    _add(checks, f"{label}_has_traces", bool(files), f"{len(files)} trace file(s)",
         "the run produced no trace: fix collection (dftracer_tracer stage) before analysis")
    empty = [p.name for p in files if p.stat().st_size == 0]
    _add(checks, f"{label}_no_empty_traces", not empty, f"empty: {empty[:5]}" if empty else "ok",
         "an empty trace file means a rank did not finalize: re-run or drop the rank, then split/compact again")
    return files


def _analyze_check(trace_dir: str, checkpoint_dir: str, baseline: str, variant: str, analysis_output: str, expected_processes: int) -> str:
    checks: List[dict] = []
    if trace_dir:
        _check_trace_dir(checks, "trace_dir", trace_dir)
    if checkpoint_dir:
        c = Path(checkpoint_dir)
        views = sorted(c.glob("_flat_view_*.parquet")) if c.is_dir() else []
        _add(checks, "checkpoint_flat_views", bool(views) and all(v.stat().st_size > 0 for v in views),
             f"{len(views)} _flat_view_*.parquet in {c}",
             "re-run analyze with analyzer.checkpoint=True and analyzer.checkpoint_dir=<dir>; generic preset first")
        stats = sorted(c.glob("_raw_stats_*.json")) if c.is_dir() else []
        ok_stats = True
        for s in stats:
            try:
                json.loads(s.read_text())
            except (OSError, ValueError):
                ok_stats = False
        _add(checks, "checkpoint_raw_stats", bool(stats) and ok_stats, f"{len(stats)} _raw_stats_*.json (parse ok: {ok_stats})",
             "the checkpoint is incomplete: delete it and re-run analyze")
    if baseline or variant:
        _add(checks, "comparison_has_both_runs", bool(baseline and variant),
             f"baseline={baseline or '(missing)'} variant={variant or '(missing)'}",
             "a comparison needs both the baseline and the variant (optimized) run's compact trace directories")
        if baseline and variant:
            same = Path(baseline).resolve() == Path(variant).resolve()
            _add(checks, "comparison_runs_distinct", not same, "baseline and variant differ" if not same else "same directory twice",
                 "pass the optimized run's own trace directory as variant")
            _check_trace_dir(checks, "baseline", baseline)
            _check_trace_dir(checks, "variant", variant)
    if analysis_output:
        out = analysis_output.strip()
        bad = out.startswith("dfanalyzer exited with code") or out == "(no output)" or not out
        _add(checks, "analysis_output_ok", not bad, out[:160], "read the stderr in the output; index the directory first and use the generic preset")
        if expected_processes > 0:
            m = re.search(r"(\d[\d,]*)\s+(?:processes|procs|process|ranks)\b", out, re.I)
            got = int(m.group(1).replace(",", "")) if m else None
            _add(checks, "process_count_matches", got == expected_processes,
                 f"analysis reports {got} process(es), expected {expected_processes}",
                 "analyze under-counted: index (force, 1 thread) and re-run through the analyze tool, never the bare dfanalyzer CLI; cross-check with event_count")
    return _verdict(checks)


def register(mcp):
    """Register this capability's tools on *mcp*."""

    @mcp.tool()
    def analyze(
        trace_path: str,
        view_types: Optional[List[str]] = None,
        debug: bool = False,
        verbose: bool = False,
        analyzer: str = "dftracer",
        analyzer_preset: str = "generic",
        analyzer_checkpoint: Optional[bool] = None,
        analyzer_checkpoint_dir: Optional[str] = None,
        analyzer_time_approximate: Optional[bool] = None,
        analyzer_time_granularity: Optional[float] = None,
        analyzer_time_resolution: Optional[float] = None,
        output_format: str = "console",
        output_compact: Optional[bool] = None,
        output_root_only: Optional[bool] = None,
        output_name: Optional[str] = None,
        output_run_db_path: Optional[str] = None,
        cluster_type: str = "local",
        cluster_n_workers: Optional[int] = None,
        cluster_memory_limit: Optional[str] = None,
        cluster_processes: Optional[int] = None,
        cluster_cores: Optional[int] = None,
        cluster_memory: Optional[str] = None,
        allocation_id: Optional[str] = None,
    ) -> str:
        """Run dfanalyzer on the provided trace path.

        allocation_id: Explicit Flux jobid to run this (potentially
        large-trace) analysis on, instead of the MCP server's own host.
        When omitted, the tool auto-detects the current user's most
        recent RUNNING flux allocation, if any.
        """
        # dfanalyzer truncates a dir of raw per-rank *.pfw.gz files; split +
        # index them first so it reads the full event set across all ranks.
        trace_path = _ensure_analyzable_path(trace_path)
        cmd = _hydra_args(
            trace_path=trace_path,
            view_types=view_types,
            debug=debug,
            verbose=verbose,
            analyzer=analyzer,
            analyzer_preset=analyzer_preset,
            analyzer_checkpoint=analyzer_checkpoint,
            analyzer_checkpoint_dir=analyzer_checkpoint_dir,
            analyzer_time_approximate=analyzer_time_approximate,
            analyzer_time_granularity=analyzer_time_granularity,
            analyzer_time_resolution=analyzer_time_resolution,
            output_format=output_format,
            output_compact=output_compact,
            output_root_only=output_root_only,
            output_name=output_name,
            output_run_db_path=output_run_db_path,
            cluster_type=cluster_type,
            cluster_n_workers=cluster_n_workers,
            cluster_memory_limit=cluster_memory_limit,
            cluster_processes=cluster_processes,
            cluster_cores=cluster_cores,
            cluster_memory=cluster_memory,
        )
        # dfanalyzer's dask LocalCluster is known to hang on teardown
        # after it has already printed all real output (confirmed: the
        # process sits at high CPU post-"Cluster teardown" and never
        # exits on its own). This USED to be bounded by a 300s timeout
        # (treating a timeout as success if output had already been
        # produced), but a fixed bound cannot distinguish "still hung on
        # teardown after finishing" from "still legitimately crunching a
        # large (26M+ event) trace set" — it was killing real analyses
        # before they produced any output. Per explicit request, no
        # timeout is applied here at all: analyze() now runs to
        # completion no matter how long the trace set takes. The
        # TimeoutExpired handling below is kept only as a defensive
        # fallback (e.g. if a future caller reintroduces a timeout
        # kwarg upstream); with timeout=None it will not fire.
        try:
            result = run_on_allocation(cmd, allocation_id=allocation_id, capture_output=True, text=True, timeout=None)
            stdout, stderr, returncode = result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as exc:
            # NOTE: even with text=True, TimeoutExpired.stdout/stderr are
            # returned as *bytes* (CPython does not decode the buffered
            # output when the timeout fires). Decode defensively so the
            # str concatenation below doesn't raise
            # "can't concat str to bytes" and abort the tool call.
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, (bytes, bytearray)):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, (bytes, bytearray)):
                stderr = stderr.decode("utf-8", errors="replace")
            returncode = 0 if stdout.strip() else -1
            if stderr:
                stderr += "\n[dfanalyzer timed out during dask cluster teardown after producing output above; process was killed]"
            else:
                stderr = "[dfanalyzer timed out during dask cluster teardown; process was killed]"
        if returncode != 0:
            return (
                f"dfanalyzer exited with code {returncode}\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )
        return stdout or "(no output)"

    @mcp.tool()
    def list_presets() -> str:
        """List common presets and supported modes from the docs.

        Reference:
        https://dftracer.readthedocs.io/projects/analyzer/en/latest/getting-started.html
        https://dftracer.readthedocs.io/projects/analyzer/en/latest/configuration.html
        """
        lines = [
            "dfAnalyzer Presets and Configuration",
            "====================================================",
            "",
            "ANALYZER PRESETS (analyzer/preset)",
            "- posix (default): general-purpose POSIX I/O workloads.",
            "- dlio: workloads generated by the DLIO benchmark, or apps whose",
            "  dft_event_logging categories happen to match DLIO's fixed",
            "  layer taxonomy (reader/comm/training/epoch/data_loader/etc).",
            "- generic: catch-all preset (requires dftracer-analyzer >= the",
            "  release with AnalyzerPresetConfigGeneric). Auto-discovers one",
            "  layer per DISTINCT `cat` value actually present in the trace",
            "  instead of matching a hardcoded layer-name list. USE THIS for",
            "  any app with custom dft_event_logging category strings (e.g.",
            "  \"compute\", \"preprocess\", \"communication-io\") that don't",
            "  match posix/dlio's built-in layer names — posix/dlio silently",
            "  return a returncode=0 with an EMPTY Layer Breakdown table and",
            "  zero-row flat views (no error!) when no event's `cat` matches",
            "  their fixed layer filter, even though real events were read.",
            "  RECOMMENDATION: always try `generic` first for a Python/",
            "  function-annotated app; fall back to posix/dlio only when",
            "  their specific layer semantics (POSIX call breakdown, DLIO",
            "  benchmark phases) are what you actually need.",
            "",
            "CLUSTER TYPES (cluster=)",
            "- local",
            "- slurm",
            "- lsf",
            "- pbs",
            "",
            "OUTPUT FORMATS (output=)",
            "- console (default)",
            "- csv",
            "- sqlite",
            "",
            "ANALYZER TYPES (analyzer=)",
            "- dftracer (default)",
            "- darshan",
            "- recorder",
        ]
        return "\n".join(lines)

    @mcp.tool()
    def reader(
        file: str,
        index: Optional[str] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        checkpoint_size: Optional[int] = None,
        force_rebuild: bool = False,
        check: bool = False,
        read_buffer_size: Optional[int] = None,
        mode: str = "bytes",
        index_dir: Optional[str] = None,
        allocation_id: Optional[str] = None,
    ) -> str:
        """Read bytes / lines from a GZIP or TAR.GZ compressed file.

        Corresponds to ``dftracer_reader <file> [OPTIONS]``.
        ``mode`` selects the reading mode:
        *bytes* (default) – raw byte output;
        *line_bytes* – lines with byte offsets;
        *lines* – plain text lines.

        Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
        """
        cmd = ["dftracer_reader", file]
        if index:
            cmd += ["-i", index]
        if start is not None:
            cmd += ["-s", str(start)]
        if end is not None:
            cmd += ["-e", str(end)]
        if checkpoint_size is not None:
            cmd += ["--checkpoint-size", str(checkpoint_size)]
        if force_rebuild:
            cmd.append("--force-rebuild")
        if check:
            cmd.append("--check")
        if read_buffer_size is not None:
            cmd += ["--read-buffer-size", str(read_buffer_size)]
        cmd += ["--mode", mode]
        if index_dir is not None:
            cmd += ["--index-dir", index_dir]
        result = run_on_allocation(cmd, allocation_id=allocation_id, capture_output=True, text=True, check=True)
        return result.stdout

    @mcp.tool()
    def info(
        files: Optional[str] = None,
        directory: Optional[str] = None,
        query_type: str = "summary",
        verbose: bool = False,
        force_rebuild: bool = False,
        checkpoint_size: Optional[int] = None,
        index_dir: Optional[str] = None,
        executor_threads: Optional[int] = None,
        allocation_id: Optional[str] = None,
    ) -> str:
        """Display metadata / index info for .pfw.gz files.

        Corresponds to ``dftracer_info [OPTIONS]`` — no positional arg.
        Use ``--files <...>`` or ``-d <dir>`` to select files.

        ``query_type`` selects the output style:
        *summary* (default) – aggregate all files;
        *detailed* – per-file output.

        Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
        """
        cmd = ["dftracer_info"]
        if files is not None:
            for f in files.split():
                cmd += ["--files", f]
        if directory is not None:
            cmd += ["-d", directory]
        cmd += ["--query", query_type]
        if verbose:
            cmd.append("-v")
        if force_rebuild:
            cmd.append("--force-rebuild")
        if checkpoint_size is not None:
            cmd += ["--checkpoint-size", str(checkpoint_size)]
        if index_dir is not None:
            cmd += ["--index-dir", index_dir]
        if executor_threads is not None:
            cmd += ["--executor-threads", str(executor_threads)]
        result = run_on_allocation(cmd, allocation_id=allocation_id, capture_output=True, text=True, check=True)
        return result.stdout

    @mcp.tool()
    def event_count(
        directory: Optional[str] = None,
        force: bool = False,
        checkpoint_size: Optional[int] = None,
        index_dir: Optional[str] = None,
        executor_threads: Optional[int] = None,
        allocation_id: Optional[str] = None,
    ) -> str:
        """Count valid events in .pfw/.pfw.gz files.

        ``dftracer_event_count [OPTIONS]`` — directory-scoped.
        Falls back to the current directory when none is provided.

        Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
        """
        cmd = ["dftracer_event_count"]
        if directory is not None:
            cmd += ["-d", directory]
        else:
            cmd += ["-d", "."]
        if force:
            cmd.append("-f")
        if checkpoint_size is not None:
            cmd += ["--checkpoint-size", str(checkpoint_size)]
        if index_dir is not None:
            cmd += ["--index-dir", index_dir]
        if executor_threads is not None:
            cmd += ["--executor-threads", str(executor_threads)]
        result = run_on_allocation(cmd, allocation_id=allocation_id, capture_output=True, text=True, check=True)
        return result.stdout

    @mcp.tool()
    def split(
        app_name: Optional[str] = None,
        directory: str = ".",
        output_dir: Optional[str] = None,
        chunk_size_mb: int = 4,
        force: bool = False,
        compress: bool = True,
        verify: bool = False,
        verbose: bool = False,
        checkpoint_size: Optional[int] = None,
        index_dir: Optional[str] = None,
        executor_threads: Optional[int] = None,
        allocation_id: Optional[str] = None,
    ) -> str:
        """Split traces into equal-size chunks.

        ``dftracer_split [OPTIONS]`` — operates on a directory.
        ``app_name`` becomes the prefix for output files; when missing
        the default "app" is used so the tool still works but the user
        should pass an explicit name.

        Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
        """
        cmd = ["dftracer_split"]
        if app_name:
            cmd += ["-n", app_name]
        else:
            cmd += ["-n", "app"]
        if directory:
            cmd += ["-d", directory]
        if output_dir is not None:
            cmd += ["-o", output_dir]
        else:
            cmd += ["-o", "./split"]
        if chunk_size_mb != 4:
            cmd += ["-s", str(int(chunk_size_mb))]
        if verify:
            cmd.append("--verify")
        if force:
            cmd.append("-f")
        if not compress:
            cmd.append("--compress=false")
        if verbose:
            cmd.append("-v")
        if checkpoint_size is not None:
            cmd += ["--checkpoint-size", str(checkpoint_size)]
        if index_dir is not None:
            cmd += ["--index-dir", index_dir]
        if executor_threads is not None:
            cmd += ["--executor-threads", str(executor_threads)]
        run_on_allocation(cmd, allocation_id=allocation_id, check=True, capture_output=True, text=True)
        return f"Split trace files → {output_dir}"

    @mcp.tool()
    def stats(
        directory: Optional[str] = None,
        files: Optional[str] = None,
        index_dir: Optional[str] = None,
        report: str = "summary",
        top_n: int = 10,
        top_n_pid_tid: int = 10,
        query: Optional[str] = None,
        group_by_dims: Optional[str] = None,
        json_output: bool = False,
        no_auto_index: bool = False,
        checkpoint_size: Optional[int] = None,
        executor_threads: Optional[int] = None,
        allocation_id: Optional[str] = None,
    ) -> str:
        """Compute event statistics (summary / categories / names / pid_tids …).

        Uses ``dftracer_stats [OPTIONS]`` — directory-scoped.
        Valid ``report`` values are documented in the CLI reference:
        *summary*, *categories*, *names*, *pid_tids*, *time_range*,
        *duration*, *top-names*, *top-categories*, *detailed*.

        Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
        """
        cmd = ["dftracer_stats"]
        if directory:
            cmd += ["-d", directory]
        else:
            cmd += ["-d", "."]
        if files is not None:
            for f in files.split():
                cmd += ["--files", f]
        if index_dir is not None:
            cmd += ["--index-dir", index_dir]
        cmd += ["--report", report]
        cmd += ["--top-n", str(top_n)]
        cmd += ["--top-n-pid-tid", str(top_n_pid_tid)]
        if query is not None:
            cmd += ["--query", query]
        if group_by_dims is not None:
            for dim in group_by_dims.split():
                cmd += ["--group-by", dim]
        if json_output:
            cmd.append("--json")
        if no_auto_index:
            cmd.append("--no-auto-index")
        if checkpoint_size is not None:
            cmd += ["--checkpoint-size", str(checkpoint_size)]
        if executor_threads is not None:
            cmd += ["--executor-threads", str(executor_threads)]
        result = run_on_allocation(cmd, allocation_id=allocation_id, capture_output=True, text=True, check=True)
        return result.stdout

    @mcp.tool()
    def aggregator(
        directory: Optional[str] = None,
        output_file: str = "aggregated_output.json",
        time_interval_ms: float = 5000.0,
        group_keys: Optional[str] = None,
        metric_fields: Optional[str] = None,
        query: Optional[str] = None,
        force: bool = False,
        checkpoint_size: Optional[int] = None,
        index_dir: Optional[str] = None,
        executor_threads: Optional[int] = None,
        compress: bool = False,
        compression_level: int = 6,
        event_format: str = "counter",
        boundary_events: Optional[str] = None,
        no_track_process_parents: bool = False,
        chunk_size_mb: int = 4,
        read_batch_size_mb: int = 4,
        compute_percentiles: bool = False,
        percentiles: Optional[str] = None,
        relative_accuracy: float = 0.01,
        format_type: str = "json",
        allocation_id: Optional[str] = None,
    ) -> str:
        """Aggregate DFTracer events into time-series counters.

        ``dftracer_aggregator [OPTIONS]`` — directory-scoped.
        Supports JSON or Arrow IPC output; also percentile computation.

        *event_format* selects the source rows for aggregation:
        **counter** (default) – counter rows with category=sys;
        **regular** – regular event rows from non-counter events;
        **profile-counter** – counter rows whose category is NOT sys.

        Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
        """
        cmd = ["dftracer_aggregator"]
        if directory:
            cmd += ["-d", directory]
        else:
            cmd += ["-d", "."]
        cmd += ["-o", output_file]
        cmd += ["-t", str(time_interval_ms)]
        if group_keys is not None:
            cmd += ["-g", group_keys]
        if metric_fields is not None:
            cmd += ["-m", metric_fields]
        if query is not None:
            cmd += ["--query", query]
        if force:
            cmd.append("-f")
        if checkpoint_size is not None:
            cmd += ["--checkpoint-size", str(checkpoint_size)]
        if index_dir is not None:
            cmd += ["--index-dir", index_dir]
        if executor_threads is not None:
            cmd += ["--executor-threads", str(executor_threads)]
        if compress:
            cmd.append("--compress")
        cmd += ["--compression-level", str(compression_level)]
        cmd += ["--event-format", event_format]
        if boundary_events is not None:
            cmd += ["--boundary-events", boundary_events]
        if no_track_process_parents:
            cmd.append("--no-track-process-parents")
        if chunk_size_mb != 4:
            cmd += ["--chunk-size", str(chunk_size_mb)]
        if read_batch_size_mb != 4:
            cmd += ["--read-batch-size", str(read_batch_size_mb)]
        if compute_percentiles:
            cmd.append("--compute-percentiles")
        if percentiles is not None:
            cmd += ["--percentiles", percentiles]
        cmd += ["--relative-accuracy", str(relative_accuracy)]
        cmd += ["--format", format_type]
        run_on_allocation(cmd, allocation_id=allocation_id, check=True, capture_output=True, text=True)
        return f"Aggregated traces → {output_file}"

    @mcp.tool()
    def comparator(
        baseline: Optional[str] = None,
        variant: Optional[str] = None,
        config_path: Optional[str] = None,
        query: str = 'cat == "POSIX" OR cat == "STDIO"',
        group_by_dims: str = "cat,name",
        output_format: str = "table",
        time_interval_ms: float = 5000.0,
        threshold_pct: float = 0.0,
        no_color: bool = False,
        executor_threads: Optional[int] = None,
        index_dir: Optional[str] = None,
        force: bool = False,
        checkpoint_size: Optional[int] = None,
        allocation_id: Optional[str] = None,
    ) -> str:
        """Compare trace metrics between a baseline and variant run.

        ``dftracer_comparator [OPTIONS]`` — no positional args; use --baseline /
        --variant or the single --config flag (not all three can be used together).
        Reports delta%, Cohen's d significance (NEGLIGIBLE|SMALL|MEDIUM|LARGE).

        Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
        """
        cmd = ["dftracer_comparator"]
        if baseline is not None:
            cmd += ["--baseline", baseline]
        if variant is not None:
            cmd += ["--variant", variant]
        if config_path is not None:
            cmd += ["--config", config_path]
        cmd += ["--query", query]
        # CLI accepts a single --group-by with comma-separated keys.
        if group_by_dims:
            cmd += ["--group-by", group_by_dims]
        cmd += ["--format", output_format]
        cmd += ["-t", str(time_interval_ms)]
        cmd += ["--threshold", str(threshold_pct)]
        if no_color:
            cmd.append("--no-color")
        if executor_threads is not None:
            cmd += ["--executor-threads", str(executor_threads)]
        if index_dir is not None:
            cmd += ["--index-dir", index_dir]
        if force:
            cmd.append("--force")
        if checkpoint_size is not None:
            cmd += ["--checkpoint-size", str(checkpoint_size)]
        result = run_on_allocation(cmd, allocation_id=allocation_id, capture_output=True, text=True, check=True)
        return result.stdout

    @mcp.tool()
    def view(
        files: Optional[str] = None,
        directory: Optional[str] = None,
        preset: Optional[str] = None,
        recipe: Optional[str] = None,
        save_recipe: Optional[str] = None,
        query: Optional[str] = None,
        time_range: Optional[str] = None,
        min_duration: Optional[int] = None,
        max_duration: Optional[int] = None,
        output_file: Optional[str] = None,
        stream: bool = False,
        no_metadata: bool = True,
        index_dir: Optional[str] = None,
        no_auto_index: bool = False,
        checkpoint_size: Optional[int] = None,
        executor_threads: Optional[int] = None,
        allocation_id: Optional[str] = None,
    ) -> str:
        """Extract a filtered subset of trace events with chunk-level pruning.

        Returns NDJSON — one JSON event object per line.  The tool builds
        bloom-filter indices on first use so subsequent queries over the same
        directory are fast.

        **Query DSL** (``--query`` field) — case-sensitive string matching:

        * Fields: ``cat``, ``name``, ``dur`` (µs), ``ph``, ``pid``, ``tid``,
          ``ts`` (µs), ``id``
        * Nested args: ``args.comp``, ``args.fhash``, ``args.flags``, etc.
        * Operators: ``==``, ``!=``, ``>``, ``<``, ``>=``, ``<=``
        * Boolean: ``and`` (lower), ``OR`` (upper)
        * String values **must** match case exactly: ``"POSIX"`` not ``"posix"``

        **Example queries**::

            'cat == "POSIX"'
            'cat == "POSIX" and name == "open"'
            'cat == "POSIX" OR cat == "STDIO"'
            'cat == "C_APP" and args.comp == "io"'
            'cat == "POSIX" and dur > 1000'
            'cat == "POSIX" and name != "lseek"'

        **Presets** (``--preset``):
            * ``io``      — STDIO-level I/O events
            * ``compute`` — C_APP compute spans
            * ``dlio``    — deep-learning I/O workload events

        **Time / duration filters**:
            * ``time_range`` — ``"min_us,max_us"`` e.g. ``"0,500000"``
            * ``min_duration`` / ``max_duration`` — event duration in µs

        ``no_metadata=True`` (default) strips ``ph=M`` hash-mapping events
        from output.  Set ``False`` to include FH (file-hash→path) events.

        Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
        """
        cmd = ["dftracer_view"]
        if files is not None:
            for f in files.split():
                cmd += ["--files", f]
        if directory is not None:
            cmd += ["-d", directory]
        if preset is not None:
            cmd += ["--preset", preset]
        if recipe is not None:
            cmd += ["--recipe", recipe]
        if save_recipe is not None:
            cmd += ["--save-recipe", save_recipe]
        if query is not None:
            cmd += ["--query", query]
        if time_range is not None:
            cmd += ["--time-range", time_range]
        if min_duration is not None:
            cmd += ["--min-duration", str(min_duration)]
        if max_duration is not None:
            cmd += ["--max-duration", str(max_duration)]
        if output_file:
            cmd += ["-o", output_file]
        if no_metadata:
            cmd.append("--no-metadata")
        if stream:
            cmd.append("--stream")
        if index_dir is not None:
            cmd += ["--index-dir", index_dir]
        if no_auto_index:
            cmd.append("--no-auto-index")
        if checkpoint_size is not None:
            cmd += ["--checkpoint-size", str(checkpoint_size)]
        if executor_threads is not None:
            cmd += ["--executor-threads", str(executor_threads)]
        result = run_on_allocation(cmd, allocation_id=allocation_id, capture_output=True, text=True, check=True)
        return result.stdout

    @mcp.tool()
    def index(
        directory: Optional[str] = None,
        dimensions: Optional[str] = None,
        force: bool = False,
        checkpoint_size: Optional[int] = None,
        executor_threads: Optional[int] = None,
        index_dir: Optional[str] = None,
        expected_entries: int = 1024,
        false_positive_rate: float = 0.01,
        read_batch_size_mb: int = 4,
        manifest: bool = False,
        rebuild_summaries: bool = False,
        allocation_id: Optional[str] = None,
    ) -> str:
        """Build bloom-filter per-chunk indices (and optional manifest).

        ``dftracer_index [OPTIONS]`` — directory-scoped.
        Uses shared indexing flags; supports arbitrary dimension keys and
        manifest tables for checkpoint/event-line routing.  Bloom filter
        sizing is controlled by *expected_entries* and *false_positive_rate*.

        Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
        """
        cmd = ["dftracer_index"]
        if directory:
            cmd += ["-d", directory]
        else:
            cmd.append("-d")
            cmd.append(".")
        if dimensions is not None:
            cmd += ["--dimensions", dimensions]
        if force:
            cmd.append("-f")
        if checkpoint_size is not None:
            cmd += ["--checkpoint-size", str(checkpoint_size)]
        if executor_threads is not None:
            cmd += ["--executor-threads", str(executor_threads)]
        if index_dir is not None:
            cmd += ["--index-dir", index_dir]
        # --index-dir defaults to the data directory; we omit it when the user
        # didn't specify anything so the built-in default (same as data files) applies.
        cmd += ["--expected-entries", str(expected_entries)]
        cmd += ["--false-positive-rate", str(false_positive_rate)]
        cmd += ["--read-batch-size", str(read_batch_size_mb)]
        if manifest:
            cmd.append("--manifest")
        if rebuild_summaries:
            cmd.append("--rebuild-summaries")
        run_on_allocation(cmd, allocation_id=allocation_id, check=True, capture_output=True, text=True)
        return f"Bloom index built in directory '{directory or '.'}'"

    @mcp.tool()
    def aggregator_mpi(
        directory: Optional[str] = None,
        output_file: str = "aggregated_output.json.gz",
        time_interval_ms: float = 5000.0,
        staging_dir: Optional[str] = None,
        shared_staging: Optional[str] = None,
        keep_staging: bool = False,
        checkpoint_size: Optional[int] = None,
        executor_threads: Optional[int] = None,
        allocation_id: Optional[str] = None,
    ) -> str:
        """Distributed-SST aggregator driven via ``mpirun``.

        Runs a five-task DAG *scan → phase_a → phase_b → phase_c → merge* across MPI
        ranks.  Each rank produces per-rank aggregation SSTs; rank~0 bulk-ingests and the
        ranks jointly write the final gzip JSON output.  Requires a build configured with
        :envvar:`DFTRACER_UTILS_ENABLE_MPI=ON`.

        *staging_dir* is the node-local SST staging root; when set alongside *shared_staging*,
        rank-sidecar artefacts are moved across the shared filesystem before coordinator ingest.

        Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
        """
        cmd = ["dftracer_aggregator_mpi"]
        if directory is not None:
            cmd += ["-d", directory]
        else:
            cmd.append("-d")
            cmd.append(".")
        cmd += ["-o", output_file]
        cmd += ["-t", str(time_interval_ms)]
        if staging_dir is not None:
            cmd += ["--staging-dir", staging_dir]
        if shared_staging is not None:
            cmd += ["--shared-staging", shared_staging]
        if keep_staging:
            cmd.append("--keep-staging")
        if checkpoint_size is not None:
            cmd += ["--checkpoint-size", str(checkpoint_size)]
        if executor_threads is not None:
            cmd += ["--executor-threads", str(executor_threads)]
        # aggregator_mpi is designed to be invoked through mpirun; we still try to run it
        # but return a message noting the expected invocation pattern.
        run_on_allocation(cmd, allocation_id=allocation_id, check=False, capture_output=True, text=True)
        return (
            f"Note: dftracer_aggregator_mpi must be run via mpirun.\n"
            f"  mpirun -n <NP> dftracer_aggregator_mpi " + " ".join(cmd[1:])
        )

    @mcp.tool()
    def call_tree_mpi(
        input_dir: str = "",
        output_file: str = "call_tree.pfw",
        staging_dir: Optional[str] = None,
        gzip_: bool = False,
        verbose: bool = False,
        keep_staging: bool = False,
        allocation_id: Optional[str] = None,
    ) -> str:
        """Distributed call-tree aggregation across MPI ranks.

        ``dftracer_call_tree_mpi <input_dir> [OPTIONS]`` — *input_dir* is the input directory
        containing trace files (required positional argument on the CLI).  Produces a merged
        output that may optionally be gzipped and staged in a shared filesystem before merge.

        Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
        """
        cmd = ["dftracer_call_tree_mpi"]
        # Input dir is the sole positional argument per the CLI docs.
        if input_dir:
            cmd.append(input_dir)
        else:
            cmd.append(".")  # default placeholder
        cmd += ["-o", output_file]
        if staging_dir is not None:
            cmd += ["--staging-dir", staging_dir]
        if gzip_:
            cmd.append("--gzip")
        if verbose:
            cmd.append("-v")
        if keep_staging:
            cmd.append("--keep-staging")
        # Like aggregator_mpi, this typically runs under mpirun.
        run_on_allocation(cmd, allocation_id=allocation_id, check=False, capture_output=True, text=True)
        return (
            f"Note: dftracer_call_tree_mpi should be invoked via mpirun.\n"
            f"  mpirun -n <NP> " + " ".join(cmd)
        )


    @mcp.tool()
    def dftracer_analyze_check(trace_dir: str = "", checkpoint_dir: str = "", baseline: str = "", variant: str = "",
                               analysis_output: str = "", expected_processes: int = 0) -> str:
        """Deterministically validate what the analysis stage used and produced; never runs dfanalyzer.

        Use before trusting analyze/comparator numbers. Give any of: trace_dir (compact trace directory: exists, has
        non-empty .pfw/.pfw.gz files), checkpoint_dir (dfanalyzer checkpoint: _flat_view_*.parquet and parseable
        _raw_stats_*.json), baseline and variant (a comparison needs both runs, distinct, each with traces),
        analysis_output (the analyze tool's text: not an error, not empty) with expected_processes (the process count it
        reports must equal the number of ranks that ran: catches the known silent under-count).

        Returns JSON with keys: status ("pass" | "fail" | "inconclusive" when nothing was given), checks (check, ok,
        detail, fix), failed.
        """
        return _analyze_check(trace_dir, checkpoint_dir, baseline, variant, analysis_output, expected_processes)
