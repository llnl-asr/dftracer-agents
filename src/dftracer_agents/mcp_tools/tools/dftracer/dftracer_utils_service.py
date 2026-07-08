#!/usr/bin/env python3
"""DFTracer Utils MCP Service — 21 tools aligned with the official CLI docs.

This is the largest service in the dftracer-agents package.  It wraps every
documented ``dftracer_*`` CLI binary as a thin MCP tool and exposes them as a
collection of categorised sub-servers:

Sub-server groupings
--------------------
* **core** (``DFTracerCore``) — reader, info, merge, split, event_count,
  pgzip, tar
* **analysis** (``DFTracerAnalysis``) — stats, aggregator, call_tree,
  comparator
* **query** (``DFTracerQuery``) — view, index, organize, reconstruct
* **utility** (``DFTracerUtility``) — replay, server, gen_fake_trace
* **dlio** (``DFTracerDLIO``) — gen_dlio_config
* **synthetic** (``DFTracerSynthetic``) — reserved sub-server (tools currently
  registered on *utility*)
* **mpi** (``DFTracerMPI``) — aggregator_mpi, call_tree_mpi

Each registered tool is a thin MCP wrapper around a ``subprocess.run`` call
that forwards its arguments to the corresponding ``dftracer_*`` binary.  No
business logic is duplicated here; the binaries are the source of truth.

CLI reference:
    https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
"""

import subprocess
from typing import List, Optional
from fastmcp import FastMCP

from ...mcp_service_factory import MCPService
from ..session.session_tools import _session_split_traces_impl, _session_validate_traces_impl


# ── shared / default helpers ───────────────────────────────────────────

def _build_watchdog_flags(
    disable_watchdog: bool = False,
    watchdog_global_timeout: Optional[float] = None,
    watchdog_task_timeout: Optional[float] = None,
    watchdog_interval: Optional[float] = None,
    watchdog_warning_threshold: Optional[float] = None,
    watchdog_idle_timeout: Optional[float] = None,
    watchdog_deadlock_timeout: Optional[float] = None,
) -> list[str]:
    """Build the shared watchdog CLI flags accepted by many ``dftracer_*`` tools.

    Several binaries expose a common set of watchdog/timeout options.  This
    helper converts the Python keyword arguments into the corresponding
    ``--watchdog-*`` flags so that individual tool wrappers do not have to
    duplicate the translation logic.

    Args:
        disable_watchdog: When ``True``, appends ``--disable-watchdog``,
            which turns off the watchdog thread entirely.
        watchdog_global_timeout: Maximum wall-clock seconds the whole process
            is allowed to run before the watchdog aborts it.  Maps to
            ``--watchdog-global-timeout <value>``.
        watchdog_task_timeout: Maximum seconds allowed per individual task
            before the watchdog fires.  Maps to
            ``--watchdog-task-timeout <value>``.
        watchdog_interval: How often (in seconds) the watchdog thread polls
            for stalls.  Maps to ``--watchdog-interval <value>``.
        watchdog_warning_threshold: Fraction of a timeout that triggers a
            warning log before the hard abort.  Maps to
            ``--watchdog-warning-threshold <value>``.
        watchdog_idle_timeout: Seconds of inactivity before the watchdog
            considers the process idle and terminates it.  Maps to
            ``--watchdog-idle-timeout <value>``.
        watchdog_deadlock_timeout: Seconds before the watchdog declares a
            deadlock and aborts.  Maps to
            ``--watchdog-deadlock-timeout <value>``.

    Returns:
        A list of strings suitable for appending to a ``subprocess`` command
        list.  An empty list is returned when all parameters are at their
        defaults (i.e. the watchdog is left unconfigured).

    Note:
        This function does not invoke any subprocess; callers are responsible
        for extending their ``cmd`` list with the returned flags before passing
        it to ``subprocess.run``.
    """
    args: list[str] = []
    if disable_watchdog:
        args.append("--disable-watchdog")
    if watchdog_global_timeout is not None:
        args.extend(["--watchdog-global-timeout", str(watchdog_global_timeout)])
    if watchdog_task_timeout is not None:
        args.extend(["--watchdog-task-timeout", str(watchdog_task_timeout)])
    if watchdog_interval is not None:
        args.extend(["--watchdog-interval", str(watchdog_interval)])
    if watchdog_warning_threshold is not None:
        args.extend(["--watchdog-warning-threshold", str(watchdog_warning_threshold)])
    if watchdog_idle_timeout is not None:
        args.extend(["--watchdog-idle-timeout", str(watchdog_idle_timeout)])
    if watchdog_deadlock_timeout is not None:
        args.extend(["--watchdog-deadlock-timeout", str(watchdog_deadlock_timeout)])
    return args


class DftracerUtilsService(MCPService):
    """MCP service that wraps every documented ``dftracer_*`` CLI binary.

    ``DftracerUtilsService`` is the largest service in the dftracer-agents
    package.  It registers 21 MCP tools across seven categorised
    ``FastMCP`` sub-servers.  The sub-server grouping mirrors the logical
    taxonomy in the official CLI documentation so that clients can mount
    only the sub-server(s) they need rather than the full combined server.

    Each tool is a *thin wrapper*: it translates Python keyword arguments
    into a ``subprocess`` command list and delegates all real work to the
    corresponding ``dftracer_*`` binary.  No parsing or business logic is
    duplicated here.

    Attributes:
        core_subservice (FastMCP): Sub-server named ``"DFTracerCore"``.
            Hosts the following tools: ``reader``, ``info``, ``merge``,
            ``split``, ``event_count``, ``pgzip``, ``tar``.
        analysis_subservice (FastMCP): Sub-server named
            ``"DFTracerAnalysis"``.  Hosts: ``stats``, ``aggregator``,
            ``call_tree``, ``comparator``.
        query_subservice (FastMCP): Sub-server named ``"DFTracerQuery"``.
            Hosts: ``view``, ``index``, ``organize``, ``reconstruct``.
        utility_subservice (FastMCP): Sub-server named
            ``"DFTracerUtility"``.  Hosts: ``replay``, ``server``,
            ``gen_fake_trace``.
        dlio_subservice (FastMCP): Sub-server named ``"DFTracerDLIO"``.
            Hosts: ``gen_dlio_config``.
        synthetic_subservice (FastMCP): Sub-server named
            ``"DFTracerSynthetic"``.  Reserved for future synthetic-trace
            tools; ``gen_fake_trace`` is currently registered on
            *utility_subservice* for historical reasons.
        mpi_subservice (FastMCP): Sub-server named ``"DFTracerMPI"``.
            Hosts the MPI-parallel variants: ``aggregator_mpi``,
            ``call_tree_mpi``.
    """

    def __init__(self):
        self.core_subservice = FastMCP("DFTracerCore")       # read, merge, split…
        self.analysis_subservice = FastMCP("DFTracerAnalysis")  # stats, aggregator…
        self.query_subservice = FastMCP("DFTracerQuery")        # view, index…
        self.utility_subservice = FastMCP("DFTracerUtility")    # replay, reconstruct…
        self.dlio_subservice = FastMCP("DFTracerDLIO")          # gen_dlio_config
        self.synthetic_subservice = FastMCP("DFTracerSynthetic")  # gen_fake_trace
        self.mpi_subservice = FastMCP("DFTracerMPI")            # _mpi variants
        self.session_subservice = FastMCP("DFTracerUtilsSession")  # session-aware wrappers

        self._register_core_tools()
        self._register_analysis_tools()
        self._register_query_tools()
        self._register_organize_tools()
        self._register_replay_tools()
        self._register_dlio_tools()
        self._register_synthetic_tools()
        self._register_server_tool()
        self._register_mpi_tools()
        self._register_session_tools()

    # ── Core tools (reader, info, merge, split, event_count, pgzip) ───

    def _register_core_tools(self):
        """Register the core file-IO tools on ``core_subservice``.

        Registers the following MCP tools:

        * ``reader`` — reads raw bytes or lines from a GZIP or TAR.GZ
          compressed DFTracer trace file via ``dftracer_reader``.
        * ``info`` — displays index metadata and summary/detailed statistics
          for ``.pfw.gz`` files via ``dftracer_info``.
        * ``merge`` — merges multiple ``.pfw`` / ``.pfw.gz`` trace files
          into a single JSON-array output via ``dftracer_merge``.
        * ``split`` — splits trace files in a directory into equal-size
          chunks via ``dftracer_split``.
        * ``event_count`` — counts the number of valid events across
          ``.pfw`` / ``.pfw.gz`` files in a directory via
          ``dftracer_event_count``.
        * ``pgzip`` — parallel-gzips ``.pfw`` files to ``.pfw.gz`` in a
          directory via ``dftracer_pgzip``.
        * ``tar`` — inspects or lists files inside a TAR.GZ archive
          containing DFTracer data via ``dftracer_tar``.
        """
        @self.core_subservice.tool()
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
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout

        @self.core_subservice.tool()
        def info(
            files: Optional[str] = None,
            directory: Optional[str] = None,
            query_type: str = "summary",
            verbose: bool = False,
            force_rebuild: bool = False,
            checkpoint_size: Optional[int] = None,
            index_dir: Optional[str] = None,
            executor_threads: Optional[int] = None,
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
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout

        @self.core_subservice.tool()
        def merge(
            directory: Optional[str] = None,
            output_file: str = "combined.pfw",
            force: bool = False,
            compress: bool = False,
            gzip_only: bool = False,
            verbose: bool = False,
            checkpoint_size: Optional[int] = None,
            index_dir: Optional[str] = None,
            executor_threads: Optional[int] = None,
        ) -> str:
            """Merge .pfw/.pfw.gz files into a single JSON-array output.

            Uses ``dftracer_merge [OPTIONS]`` — operates on a directory.
            When ``compress`` is True the output is gzipped; when ``gzip_only``
            is True only .pfw.gz input files are processed (plain .pfw files
            are ignored).

            Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
            """
            cmd = ["dftracer_merge"]
            if directory:
                cmd += ["-d", directory]
            cmd += ["-o", output_file]
            if force:
                cmd.append("-f")
            if compress:
                cmd.append("-c")
            if gzip_only:
                cmd.append("-g")
            if verbose:
                cmd.append("-v")
            if checkpoint_size is not None:
                cmd += ["--checkpoint-size", str(checkpoint_size)]
            if index_dir is not None:
                cmd += ["--index-dir", index_dir]
            if executor_threads is not None:
                cmd += ["--executor-threads", str(executor_threads)]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return f"Merged trace files from {directory} → {output_file}"

        @self.core_subservice.tool()
        def split(
            app_name: Optional[str] = None,
            directory: str = ".",
            output_dir: Optional[str] = None,
            chunk_size_mb: float = 4.0,
            force: bool = False,
            compress: bool = True,
            verify: bool = False,
            verbose: bool = False,
            checkpoint_size: Optional[int] = None,
            index_dir: Optional[str] = None,
            executor_threads: Optional[int] = None,
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
            if chunk_size_mb != 4.0:
                cmd += ["-s", str(chunk_size_mb)]
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
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return f"Split trace files → {output_dir}"

        @self.core_subservice.tool()
        def event_count(
            directory: Optional[str] = None,
            force: bool = False,
            checkpoint_size: Optional[int] = None,
            index_dir: Optional[str] = None,
            executor_threads: Optional[int] = None,
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
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout

        @self.core_subservice.tool()
        def pgzip(
            directory: Optional[str] = None,
            verbose: bool = False,
            executor_threads: Optional[int] = None,
        ) -> str:
            """Parallel-gzip .pfw → .pfw.gz in a directory.

            ``dftracer_pgzip [OPTIONS]`` — directory-scoped.
            Defaults to the current directory when none is provided.

            Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
            """
            cmd = ["dftracer_pgzip"]
            if directory:
                cmd += ["-d", directory]
            else:
                cmd += ["-d", "."]
            if verbose:
                cmd.append("-v")
            if executor_threads is not None:
                cmd += ["--executor-threads", str(executor_threads)]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            # pgzip has no stdout; return a message describing what happened.
            return "Compressed .pfw files in directory (check for *.pfw.gz)"

        @self.core_subservice.tool()
        def tar(
            file: str,
            index: Optional[str] = None,
            checkpoint_size: Optional[int] = None,
            force_rebuild: bool = False,
            list_files: bool = False,
            show_info: bool = False,
        ) -> str:
            """Inspect / list files in a TAR.GZ archive with DFTracer data.

            ``dftracer_tar [OPTIONS] <file>`` — positional .tar.gz file.
            At least one of ``list_files`` or ``show_info`` (or none for default
            behaviour) should be used; passing both causes the last flag to win.

            Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
            """
            cmd = ["dftracer_tar", file]
            if index:
                cmd += ["-i", index]
            if checkpoint_size is not None:
                cmd += ["--checkpoint-size", str(checkpoint_size)]
            if force_rebuild:
                cmd.append("-f")
            # Default action for tar with no explicit flag is to process the file,
            # but we only emit extra flags when requested.
            if list_files:
                cmd.append("--list-files")
            if show_info:
                cmd.append("--info")
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout

    # ── Analysis tools (stats, aggregator, call_tree, comparator) ─────

    def _register_analysis_tools(self):
        """Register statistical and analytical tools on ``analysis_subservice``.

        Registers the following MCP tools:

        * ``stats`` — computes event statistics (summary, categories, names,
          pid_tids, time_range, duration, top-N) via ``dftracer_stats``.
        * ``aggregator`` — aggregates DFTracer events into time-series
          counters or Arrow IPC output via ``dftracer_aggregator``.
        * ``call_tree`` — builds and analyses a hierarchical call tree from
          trace files via ``dftracer_call_tree``.
        * ``comparator`` — compares trace metrics between a baseline run and
          a variant run, reporting delta% and Cohen's d significance via
          ``dftracer_comparator``.
        """
        @self.analysis_subservice.tool()
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
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout

        @self.analysis_subservice.tool()
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
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return f"Aggregated traces → {output_file}"

        @self.analysis_subservice.tool()
        def call_tree(
            inputs: Optional[str] = None,
            recursive: bool = False,
            pattern: str = "*.pfw.gz",
            output: Optional[str] = None,
            save_json: bool = False,
            text_export: Optional[str] = None,
            max_depth: int = 0,
            analyze: bool = False,
            verbose: bool = False,
            stats_only: bool = False,
            no_save: bool = False,
            fix_c_app_names: bool = False,
        ) -> str:
            """Build and analyze a hierarchical call tree from trace files.

            ``dftracer_call_tree [OPTIONS] <inputs...>`` — positional files or dirs.
            ``stats_only`` prints statistics only (skipping tree traversal).
            ``no_save`` skips writing any output files; results are printed to stdout only.
            ``fix_c_app_names`` (default False) patches empty ``name`` / ``cat`` fields
            in the generated Chrome Tracing JSON for C_APP events by reading the
            original trace file and mapping ``id`` → ``(name, cat)``.  This works
            around a known dftracer_call_tree limitation where C_APP event names are
            stripped.

            Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
            """
            import json
            import gzip

            cmd = ["dftracer_call_tree"]
            if inputs is not None:
                cmd += inputs.split()
            if recursive:
                cmd.append("-r")
            # NOTE: dftracer_call_tree CLI does NOT support --pattern;
            # the pattern parameter is ignored here and used only for
            # documentation / filtering at the Python layer.
            actual_output = output
            if output:
                cmd += ["-o", output]
            if save_json:
                cmd.append("--json")
            if text_export:
                cmd += ["--text", text_export]
            # max_depth defaults to 0 (unlimited) on the CLI,
            # so we only emit it when the user explicitly sets a value > 0.
            if max_depth > 0:
                cmd += ["--max-depth", str(max_depth)]
            if analyze:
                cmd.append("--analyze")
            if verbose:
                cmd.append("-v")
            if stats_only:
                cmd.append("--stats-only")
            if no_save:
                cmd.append("--no-save")
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)

            # ── C_APP name restoration (workaround for dftracer_call_tree bug) ──
            if fix_c_app_names and actual_output and inputs:
                try:
                    # Build id → (name, cat) map from original trace file(s)
                    id_map: dict[int, tuple[str, str]] = {}
                    for inp in inputs.split():
                        if inp.endswith(".gz"):
                            with gzip.open(inp, "rt") as f:
                                for line in f:
                                    line = line.strip()
                                    if not line or line in ("[", "]"):
                                        continue
                                    try:
                                        ev = json.loads(line)
                                        if "id" in ev and "name" in ev and "cat" in ev:
                                            id_map[ev["id"]] = (ev["name"], ev["cat"])
                                    except json.JSONDecodeError:
                                        pass
                        elif inp.endswith(".pfw"):
                            with open(inp) as f:
                                for line in f:
                                    line = line.strip()
                                    if not line or line in ("[", "]"):
                                        continue
                                    try:
                                        ev = json.loads(line)
                                        if "id" in ev and "name" in ev and "cat" in ev:
                                            id_map[ev["id"]] = (ev["name"], ev["cat"])
                                    except json.JSONDecodeError:
                                        pass

                    # Patch the generated call_tree JSON
                    with open(actual_output) as f:
                        ct_data = json.load(f)
                    patched = 0
                    for ev in ct_data:
                        if isinstance(ev, dict) and ev.get("id") in id_map:
                            ev["name"], ev["cat"] = id_map[ev["id"]]
                            patched += 1
                    with open(actual_output, "w") as f:
                        json.dump(ct_data, f)
                    result.stdout += f"\n[call_tree] Patched {patched} C_APP event names."
                except Exception as exc:
                    result.stdout += f"\n[call_tree] Name-fix warning: {exc}"

            return result.stdout

        @self.analysis_subservice.tool()
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
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout

    # ── Query tools (view, index) ─────────────────────────────────────

    def _register_query_tools(self):
        """Register filtering and indexing tools on ``query_subservice``.

        Registers the following MCP tools:

        * ``view`` — extracts a filtered subset of trace events with
          chunk-level pruning via ``dftracer_view``; supports presets,
          custom queries, time-range filters, and recipe persistence.
        * ``index`` — builds per-chunk bloom-filter indices (and an optional
          manifest table) over a trace directory via ``dftracer_index``.
        """
        @self.query_subservice.tool()
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
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout

        @self.query_subservice.tool()
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
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return f"Bloom index built in directory '{directory or '.'}'"

    # ── Organize / Reconstruct tools ──────────────────────────────────

    def _register_organize_tools(self):
        """Register reorganisation tools on ``query_subservice``.

        Registers the following MCP tools:

        * ``organize`` — reorganises trace files into query-defined groups
          with provenance tracking via ``dftracer_organize``; each group is
          a ``name:query`` pair and produces a ``.pidx`` sidecar for later
          reconstruction.
        * ``reconstruct`` — restores original per-process trace files from
          reorganised group directories using the ``.pidx`` provenance
          sidecars written by ``organize``, via ``dftracer_reconstruct``.
        """
        @self.query_subservice.tool()
        def organize(
            files: Optional[str] = None,
            directory: Optional[str] = None,
            output_dir: str = "",
            groups: str = "",
            chunk_size_mb: int = 256,
            checkpoint_size: Optional[int] = None,
            index_dir: Optional[str] = None,
            force: bool = False,
            no_compress: bool = False,
            executor_threads: Optional[int] = None,
        ) -> str:
            """Reorganize traces into query-based groups with provenance tracking.

            ``dftracer_organize [OPTIONS] --output <dir> --groups 'io:cat == "POSIX"'
            'compute:cat == "APP"'' — each group is a colon-separated name:query pair,
            space-delimited on the CLI.  A ``--no-compress`` flag is required to store
            plain .pfw output instead of gzipped.

            Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
            """
            cmd = ["dftracer_organize"]
            if files is not None:
                for f in files.split():
                    cmd += ["--files", f]
            if directory is not None:
                cmd += ["-d", directory]
            if output_dir:
                cmd += ["-o", output_dir]
            if groups:
                # each 'name:query' pair becomes its own --groups arg.
                for g in groups.split():
                    cmd += ["--groups", g]
            cmd += ["--chunk-size", str(chunk_size_mb)]
            if checkpoint_size is not None:
                cmd += ["--checkpoint-size", str(checkpoint_size)]
            if index_dir is not None:
                cmd += ["--index-dir", index_dir]
            if force:
                cmd.append("-f")
            if no_compress:
                cmd.append("--no-compress")
            if executor_threads is not None:
                cmd += ["--executor-threads", str(executor_threads)]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return f"Reorganized traces → {output_dir or 'N/A'}"

        @self.query_subservice.tool()
        def reconstruct(
            directory: str = "",
            output: str = "reconstructed",
            index_dir: Optional[str] = None,
            checkpoint_size: Optional[int] = None,
            no_compress: bool = False,
            executor_threads: Optional[int] = None,
        ) -> str:
            """Reconstruct original traces from reorganized files via provenance sidecars.

            ``dftracer_reconstruct [OPTIONS] --directory <dir> --output <dir>`` — both
            directory and output are required on the CLI.  Uses .pidx sidecar files
            produced by ``organize``.

            Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
            """
            cmd = ["dftracer_reconstruct"]
            if directory:
                cmd += ["-d", directory]
            else:
                cmd += ["-d", "."]
            cmd += ["-o", output]
            if index_dir is not None:
                cmd += ["--index-dir", index_dir]
            if checkpoint_size is not None:
                cmd += ["--checkpoint-size", str(checkpoint_size)]
            if no_compress:
                cmd.append("--no-compress")
            if executor_threads is not None:
                cmd += ["--executor-threads", str(executor_threads)]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return f"Reconstructed traces → {output}"

    # ── Replay tool ───────────────────────────────────────────────────

    def _register_replay_tools(self):
        """Register the I/O replay tool on ``utility_subservice``.

        Registers the following MCP tool:

        * ``replay`` — replays I/O operations recorded in trace files,
          optionally respecting original timing, applying PID/TID/function/
          category filters, and driving playback from a call-tree structure,
          via ``dftracer_replay``.
        """
        @self.utility_subservice.tool()
        def replay(
            inputs: str = "",
            no_timing: bool = False,
            dry_run: bool = False,
            dftracer_mode: bool = False,
            no_sleep: bool = False,
            verbose: bool = False,
            recursive: bool = False,
            use_call_tree: bool = False,
            hierarchical_replay: bool = False,
            respect_call_hierarchy: bool = False,
            filter_pid: Optional[str] = None,
            exclude_pid: Optional[str] = None,
            filter_tid: Optional[str] = None,
            exclude_tid: Optional[str] = None,
            filter_function: Optional[str] = None,
            exclude_function: Optional[str] = None,
            filter_category: Optional[str] = None,
            exclude_category: Optional[str] = None,
            start_timestamp: Optional[int] = None,
            end_timestamp: Optional[int] = None,
            min_size: Optional[int] = None,
            max_size: Optional[int] = None,
            sample_rate: Optional[float] = None,
            sample_seed: Optional[int] = None,
            max_events: Optional[int] = None,
        ) -> str:
            """Replay I/O operations from trace files with timing / filtering.

            ``dftracer_replay [OPTIONS] <inputs...>`` — positional files or directories.
            Requires inputs (one of *files*, *directory* paths, or a directory to search).

            Key options from the CLI reference:
            *no_timing* – ignore original timestamps, execute instantly;
            *dftracer_mode* – sleep-based replay (durations) instead of real I/O;
            *no_sleep* – when used with dftracer_mode, disable sleeps entirely;
            *use_call_tree* / *hierarchical_replay* / *respect_call_hierarchy* –
            drive playback from a call-tree structure rather than flat event order.

            Filters (*--filter-* and --exclude-*) accept comma-separated values
            for multiple PIDs/TIDs/names/categories.

            Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
            """
            cmd = ["dftracer_replay"]
            if inputs:
                # Accept either space-separated paths or a single path as "inputs".
                cmd += inputs.split()
            else:
                # The CLI requires at least one positional input; fall back to current dir.
                cmd.append(".")
            if no_timing:
                cmd.append("--no-timing")
            if dry_run:
                cmd.append("--dry-run")
            if dftracer_mode:
                cmd.append("--dftracer-mode")
            if no_sleep:
                cmd.append("--no-sleep")
            if verbose:
                cmd.append("-v")
            if recursive:
                cmd.append("-r")
            if use_call_tree:
                cmd.append("--use-call-tree")
            if hierarchical_replay:
                cmd.append("--hierarchical-replay")
            if respect_call_hierarchy:
                cmd.append("--respect-call-hierarchy")
            if filter_pid is not None:
                cmd += ["--filter-pid", filter_pid]
            if exclude_pid is not None:
                cmd += ["--exclude-pid", exclude_pid]
            if filter_tid is not None:
                cmd += ["--filter-tid", filter_tid]
            if exclude_tid is not None:
                cmd += ["--exclude-tid", exclude_tid]
            if filter_function is not None:
                cmd += ["--filter-function", filter_function]
            if exclude_function is not None:
                cmd += ["--exclude-function", exclude_function]
            if filter_category is not None:
                cmd += ["--filter-category", filter_category]
            if exclude_category is not None:
                cmd += ["--exclude-category", exclude_category]
            if start_timestamp is not None:
                cmd += ["--start-timestamp", str(start_timestamp)]
            if end_timestamp is not None:
                cmd += ["--end-timestamp", str(end_timestamp)]
            if min_size is not None:
                cmd += ["--min-size", str(min_size)]
            if max_size is not None:
                cmd += ["--max-size", str(max_size)]
            if sample_rate is not None:
                cmd += ["--sample-rate", str(sample_rate)]
            if sample_seed is not None:
                cmd += ["--sample-seed", str(sample_seed)]
            if max_events is not None:
                cmd += ["--max-events", str(max_events)]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout

    # ── Utility tools (server) ────────────────────────────────────────

    def _register_server_tool(self):
        """Register the HTTP REST server tool on ``utility_subservice``.

        Registers the following MCP tool:

        * ``server`` — starts the DFTracer HTTP REST server, which exposes
          trace query endpoints over a network interface, via
          ``dftracer_server``.  The server process is started as a
          subprocess; for long-running production deployments callers should
          manage the process externally (e.g. via a daemon manager).
        """
        @self.utility_subservice.tool()
        def server(
            bind_address: str = "0.0.0.0",
            port: int = 8080,
            directory: Optional[str] = None,
            index_dir: Optional[str] = None,
            executor_threads: Optional[int] = None,
        ) -> str:
            """Start the HTTP REST server for DFTracer trace data.

            ``dftracer_server [OPTIONS] --directory <path>`` — directory is required on
            the CLI; defaults to "./traces" when omitted by this wrapper.  The function runs
            in a subprocess and returns only after the process starts (or fails).  For long-
            running instances, run the binary directly as a daemon/process group externally.

            Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
            """
            cmd = ["dftracer_server", "-b", bind_address, "-p", str(port)]
            if directory:
                cmd += ["-d", directory]
            else:
                cmd += ["-d", "traces"]
            if index_dir is not None:
                cmd += ["--index-dir", index_dir]
            if executor_threads is not None:
                cmd += ["--executor-threads", str(executor_threads)]
            subprocess.run(cmd, check=False, capture_output=True, text=True)
            return f"Started DFTracer server on {bind_address}:{port} (trace dir={directory or './traces'}) — run as daemon for long-running instance."

    # ── DLIO tools ────────────────────────────────────────────────────

    def _register_dlio_tools(self):
        """Register the DLIO configuration generator on ``dlio_subservice``.

        Registers the following MCP tool:

        * ``gen_dlio_config`` — analyses raw DFTracer traces and generates a
          DLIO YAML configuration file by fitting distribution models and
          optimising the ``max_bound`` parameter via an internal simulator,
          using ``dftracer_gen_dlio_config``.
        """
        @self.dlio_subservice.tool()
        def gen_dlio_config(
            output: str = "",
            directory: Optional[str] = None,
            max_bound_percentile: int = 95,
            simulation_iterations: int = 5,
            target_e2e_error: float = 0.05,
            target_cdf_similarity: float = 0.90,
            patience: int = 10,
            epsilon: float = 1.0,
            momentum: float = 0.9,
            min_percentile: int = 50,
            num_workers: int = 8,
            prefetch_factor: int = 2,
            seed: int = 42,
            max_samples_per_entry: int = 100,
            time_interval_ms: float = 5000.0,
            index_dir: Optional[str] = None,
            checkpoint_size: Optional[int] = None,
            executor_threads: Optional[int] = None,
            force: bool = False,
        ) -> str:
            """Generate a DLIO YAML config from raw DFTracer traces.

            ``dftracer_gen_dlio_config [OPTIONS] -o <output.yaml>`` — no positional input;
            use **-d** for the trace directory.  The tool fits distribution models and
            optimises max_bound via an internal simulator with configurable patience,
            epsilon, momentum, and min-percentile parameters.

            Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
            """
            cmd = ["dftracer_gen_dlio_config"]
            if directory:
                cmd += ["-d", directory]
            else:
                cmd.append("-d")
                cmd.append(".")
            cmd += ["-o", output]
            cmd += ["--max-bound-percentile", str(max_bound_percentile)]
            cmd += ["--simulation-iterations", str(simulation_iterations)]
            cmd += ["--target-e2e-error", str(target_e2e_error)]
            cmd += ["--target-cdf-similarity", str(target_cdf_similarity)]
            cmd += ["--patience", str(patience)]
            cmd += ["--epsilon", str(epsilon)]
            cmd += ["--momentum", str(momentum)]
            cmd += ["--min-percentile", str(min_percentile)]
            cmd += ["--num-workers", str(num_workers)]
            cmd += ["--prefetch-factor", str(prefetch_factor)]
            cmd += ["--seed", str(seed)]
            cmd += ["--max-samples-per-entry", str(max_samples_per_entry)]
            cmd += ["-t", str(time_interval_ms)]
            if index_dir is not None:
                cmd += ["--index-dir", index_dir]
            if checkpoint_size is not None:
                cmd += ["--checkpoint-size", str(checkpoint_size)]
            if executor_threads is not None:
                cmd += ["--executor-threads", str(executor_threads)]
            if force:
                cmd.append("-f")
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return f"DLIO config → {output}"

    # ── Synthetic trace tools ─────────────────────────────────────────

    def _register_synthetic_tools(self):
        """Register the synthetic trace generator on ``utility_subservice``.

        Registers the following MCP tool:

        * ``gen_fake_trace`` — generates realistic synthetic DFTracer traces
          suitable for testing bloom-filter indexing and chunk-skipping
          logic, via ``dftracer_gen_fake_trace``.  Supports optional
          end-to-end verification of the generated traces.
        """
        @self.utility_subservice.tool()
        def gen_fake_trace(
            output_dir: str = "",
            num_processes: int = 8,
            num_hosts: int = 4,
            num_epochs: int = 500,
            steps_per_epoch: int = 1000,
            checkpoint_every: int = 5,
            validation_every: int = 2,
            num_train_files: int = 8,
            num_val_files: int = 2,
            step_duration_ms: float = 100.0,
            seed: int = 42,
            verify: bool = False,
            checkpoint_size_mb: int = 2,
        ) -> str:
            """Generate realistic synthetic DFTracer traces for testing bloom-filter indexing.

            ``dftracer_gen_fake_trace [OPTIONS] --output-dir <dir>`` — no positional input;
            **--output-dir** is the one required flag.  Supports optional verification via
            bloom index + queries after generation to confirm chunk-skipping works end-to-end.

            Reference: https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html
            """
            cmd = ["dftracer_gen_fake_trace"]
            cmd += ["-o", output_dir]
            cmd += ["-p", str(num_processes)]
            cmd += ["-H", str(num_hosts)]
            cmd += ["-e", str(num_epochs)]
            cmd += ["-s", str(steps_per_epoch)]
            cmd += ["--checkpoint-every", str(checkpoint_every)]
            cmd += ["--validation-every", str(validation_every)]
            cmd += ["--num-train-files", str(num_train_files)]
            cmd += ["--num-val-files", str(num_val_files)]
            # Binary parser only accepts integers for this flag.
            cmd += ["--step-duration-ms", str(int(step_duration_ms))]
            cmd += ["--seed", str(seed)]
            if verify:
                cmd.append("--verify")
            # checkpoint-size expects bytes on the CLI when not using .pfw; we still convert MB → bytes.
            cmd += ["--checkpoint-size", str(checkpoint_size_mb * 1024 * 1024)]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return f"Synthetic traces → {output_dir}"

    # ── MPI tools (aggregator_mpi, call_tree_mpi) ─────────────────────

    def _register_mpi_tools(self):
        """Register distributed MPI tools on ``mpi_subservice``.

        Registers the following MCP tools:

        * ``aggregator_mpi`` — distributed SST aggregation driven via
          ``mpirun``, executing a five-task DAG (scan → phase_a → phase_b →
          phase_c → merge) across MPI ranks and producing a final gzip JSON
          output, via ``dftracer_aggregator_mpi``.
        * ``call_tree_mpi`` — distributed call-tree aggregation across MPI
          ranks, merging per-rank call trees into a single output file, via
          ``dftracer_call_tree_mpi``.

        Note:
            Both tools in this group require a build configured with
            ``DFTRACER_UTILS_ENABLE_MPI=ON`` and are expected to be launched
            via ``mpirun``.  The MCP wrapper invokes the binary directly; a
            hint with the correct ``mpirun`` invocation is included in the
            tool's return value.
        """
        @self.mpi_subservice.tool()
        def aggregator_mpi(
            directory: Optional[str] = None,
            output_file: str = "aggregated_output.json.gz",
            time_interval_ms: float = 5000.0,
            staging_dir: Optional[str] = None,
            shared_staging: Optional[str] = None,
            keep_staging: bool = False,
            checkpoint_size: Optional[int] = None,
            executor_threads: Optional[int] = None,
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
            subprocess.run(cmd, check=False, capture_output=True, text=True)
            return (
                f"Note: dftracer_aggregator_mpi must be run via mpirun.\n"
                f"  mpirun -n <NP> dftracer_aggregator_mpi " + " ".join(cmd[1:])
            )

        @self.mpi_subservice.tool()
        def call_tree_mpi(
            input_dir: str = "",
            output_file: str = "call_tree.pfw",
            staging_dir: Optional[str] = None,
            gzip_: bool = False,
            verbose: bool = False,
            keep_staging: bool = False,
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
            subprocess.run(cmd, check=False, capture_output=True, text=True)
            return (
                f"Note: dftracer_call_tree_mpi should be invoked via mpirun.\n"
                f"  mpirun -n <NP> " + " ".join(cmd)
            )

    # ── Session-aware tools (session_subservice) ───────────────────────

    def _register_session_tools(self) -> None:
        """Register session-aware dftracer-utils tools on :attr:`session_subservice`.

        Exposes ``session_split_traces`` — a wrapper around ``dftracer_split``
        that operates on a session workspace identified by *run_id*.
        """

        @self.session_subservice.tool()
        def session_split_traces(
            run_id: str,
            app_name: str = "app",
            run_name: str = "baseline",
        ) -> str:
            """Compact raw dftracer traces via the ``dftracer-utils`` split service.

            Reads raw ``.pfw`` / ``.pfw.gz`` files from
            ``<workspace>/<run_name>/traces/raw/`` (written by
            ``session_run_with_dftracer``) and writes compacted chunk files to
            ``<workspace>/<run_name>/traces/compact/``.

            Splitting is performed by the ``dftracer_split`` binary so that all
            dftracer-utils error handling and output formatting is applied.  For
            single files below 512 MB (uncompressed), the file is copied without
            splitting.

            Side effects:
                * Creates ``<workspace>/<run_name>/traces/compact/`` if absent.
                * Writes compacted trace chunks named ``<app_name>_*.pfw`` inside
                  ``<workspace>/<run_name>/traces/compact/``.
                * Persists ``{"step": "traces_split_<run_name>", ...}`` to
                  ``session.json``.
                * Writes an artifact log at step 12.

            Args:
                run_id:   Session identifier returned by ``session_create``.
                app_name: Prefix used for output chunk file names (default ``"app"``).
                run_name: Run label (e.g. ``"baseline"``, ``"opt1"``). Determines
                    which ``<run_name>/traces/raw/`` directory is read.
                    Defaults to ``"baseline"``.

            Returns:
                JSON string with keys ``status``, ``message``, ``run_name``,
                ``output`` (path to compact dir), and split service result keys.

            Raises:
                Returns ``{"status": "error"}`` when the raw traces directory does
                not exist or the split binary exits non-zero.

            Note:
                Must be called after ``session_run_with_dftracer`` with the same
                *run_name*.  Follow with ``session_analyze_traces`` (same
                *run_name*) to query the compacted output.
            """
            return _session_split_traces_impl(run_id=run_id, app_name=app_name, run_name=run_name)

        @self.session_subservice.tool()
        def session_validate_traces(
            run_id: str,
            run_name: str = "baseline",
            traces_dir: Optional[str] = None,
        ) -> str:
            """Validate trace files via dftracer-utils' own indexing/parsing pipeline.

            Runs ``dftracer_event_count --directory <dir> --force`` over the
            session's trace files. This forces dftracer-utils to fully parse
            and index every ``.pfw``/``.pfw.gz`` file, so a file that is
            truncated or contains malformed JSON (e.g. a worker killed
            mid-write) is surfaced by the tool's own
            ``indexed=.. skipped=.. failed=..`` summary rather than by any
            separate ad hoc content check.

            By default validates ``<workspace>/<run_name>/traces/compact/``
            if it has trace files, else ``<workspace>/<run_name>/traces/raw/``.
            Pass ``traces_dir`` to validate an arbitrary directory instead
            (e.g. a per-iteration optimization-loop trace directory).

            Args:
                run_id:     Session identifier returned by ``session_create``.
                run_name:   Run label (e.g. ``"baseline"``, ``"opt1"``).
                    Ignored when ``traces_dir`` is given.
                traces_dir: Explicit directory to validate, overriding the
                    ``run_name``-derived default.

            Returns:
                JSON string with ``status``, ``message``, ``directory``,
                ``trace_file_count``, ``indexed``, ``skipped``, ``failed``,
                and ``valid_events``. ``status`` is ``"error"`` when any file
                fails validation (``failed > 0``).
            """
            return _session_validate_traces_impl(
                run_id=run_id, run_name=run_name, traces_dir=traces_dir
            )

    # ── Execute / name (abstract contract) ────────────────────────────

    def execute(self, data: dict):
        """Satisfy the ``MCPService`` abstract interface (legacy router).

        This method is required by the ``MCPService`` base class contract but
        is not used by ``DftracerUtilsService``.  All tool dispatch is handled
        by FastMCP sub-servers registered in ``__init__``.

        Args:
            data: A dictionary that may contain a ``"command"`` key.  The
                value is not acted upon.

        Returns:
            ``None`` unconditionally.
        """
        command = data.get("command")
        return None

    @property
    def name(self) -> str:
        """Return the canonical service identifier used by ``MCPServiceFactory``.

        Returns:
            The string ``"dftracer-utils"``, which is the key under which this
            service is registered with ``MCPServiceFactory``.
        """
        return "dftracer-utils"


# ── Registration (entry-point for MCPServiceFactory) ───────────────────

from ...mcp_service_factory import MCPServiceFactory

MCPServiceFactory.register("dftracer-utils", DftracerUtilsService())  # Register singleton at import time.


def run():
    """Start a combined MCP server that exposes all dftracer-utils tools.

    Creates a single ``FastMCP`` instance named ``"DFTracerCombinedServer"``
    and populates it with every tool registered across all seven sub-servers
    (``core_subservice``, ``analysis_subservice``, ``query_subservice``,
    ``utility_subservice``, ``dlio_subservice``, ``synthetic_subservice``,
    ``mpi_subservice``).  The combined server is then started with
    ``FastMCP.run()``, which blocks until the process is terminated.

    This function is intended as the entry-point for standalone deployments
    where a single MCP server endpoint should surface all 21 tools.  For
    selective deployments, mount individual sub-servers instead.

    The combined server also includes the ``session_subservice`` tools that
    wrap dftracer-utils operations with a session ``run_id`` context.
    """
    from fastmcp import FastMCP

    combined = FastMCP("DFTracerCombinedServer")

    for attr_name in [
        "core_subservice",
        "analysis_subservice",
        "query_subservice",
        "utility_subservice",
        "dlio_subservice",
        "synthetic_subservice",
        "mpi_subservice",
    ]:
        sub = getattr(DftracerUtilsService(), attr_name, None)
        if sub:
            for cmd in sub.commands.values():
                combined.add_tool(cmd)

    combined.run()


if __name__ == "__main__":
    run()
