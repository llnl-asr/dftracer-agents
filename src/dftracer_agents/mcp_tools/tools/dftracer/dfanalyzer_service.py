"""DFAnalyzer MCP service — thin wrapper around the ``dfanalyzer`` CLI binary.

This module exposes the ``dfanalyzer`` command-line tool as a pair of MCP tools
so that AI agents can invoke trace analysis without having to construct shell
commands manually.

MCP tools exposed (all registered on :attr:`DFAnalyzerService.analyzer_subservice`):

* ``analyze`` — runs ``dfanalyzer`` against a trace directory or file,
  forwarding all Hydra-style configuration overrides as CLI flags.
* ``list_presets`` — returns a human-readable catalogue of supported analyzer
  presets, cluster backends, output formats, and analyzer types, drawn from
  the official documentation.

External binary:
    ``dfanalyzer`` — the DFTracer analysis binary that must be available on
    ``$PATH`` at runtime.  It accepts Hydra-style overrides (``--key=value``
    and ``-ahydra/...``) as well as conventional long flags (``--trace-path``,
    ``--view-type``, etc.).

Reference documentation:
    https://dftracer.readthedocs.io/projects/analyzer/en/latest/getting-started.html
    https://dftracer.readthedocs.io/projects/analyzer/en/latest/configuration.html
"""

from __future__ import annotations

import subprocess
from typing import List, Optional

from fastmcp import FastMCP

from ...mcp_service_factory import MCPService, MCPServiceFactory


def _hydra_args(
    trace_path: Optional[str] = None,
    view_types: Optional[List[str]] = None,
    debug: bool = False,
    verbose: bool = False,
    analyzer: str = "dftracer",
    analyzer_preset: str = "posix",
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
) -> List[str]:
    """Build the ``dfanalyzer`` command list from Hydra-style configuration overrides.

    Translates Python keyword arguments into the CLI flag syntax expected by
    ``dfanalyzer``.  Arguments that equal their default values are omitted from
    the output so that the binary's own defaults take effect, keeping the
    resulting command concise.

    The returned list is suitable for direct use with :func:`subprocess.run`
    (``shell=False``).

    Args:
        trace_path (Optional[str]): Filesystem path to the trace file or
            directory to analyse.  Passed as ``--trace-path <value>``.
            If ``None`` or an empty string, the flag is omitted entirely.
        view_types (Optional[List[str]]): Sequence of view-type strings to
            display in the analysis output (e.g. ``["file_name", "proc_name"]``).
            Each entry is appended as a separate ``--view-type <entry>`` pair.
            If ``None`` **or** equal to the default list
            ``["file_name", "proc_name", "time_range"]``, the flags are omitted
            so that the binary's built-in default applies.
        debug (bool): When ``True``, appends ``--debug`` to enable verbose
            debug logging in the binary.  Defaults to ``False``.
        verbose (bool): When ``True``, appends ``--verbose`` for additional
            progress output.  Defaults to ``False``.
        analyzer (str): Analyzer backend to use.  Defaults to ``"dftracer"``.
            When the value differs from the default, the Hydra group override
            ``-ahydra/analyzer=<value>`` is inserted; otherwise the flag is
            omitted.
        analyzer_preset (str): Hydra preset for the selected analyzer backend.
            Appended as ``-ahydra.analyzer/preset=<value>``.  Common values are
            ``"posix"`` (default) and ``"dlio"``.
        analyzer_checkpoint (Optional[bool]): Enable or disable checkpointing
            inside the analyzer.  Mapped to
            ``--analyzer.checkpoint=true|false``.  Omitted when ``None``.
        analyzer_time_approximate (Optional[bool]): When ``True``, instructs
            the analyzer to use approximate time-range calculations.  Mapped to
            ``--analyzer.time_approximate=true|false``.  Omitted when ``None``.
        analyzer_time_granularity (Optional[float]): Time bucket size (in
            seconds) used when aggregating trace events.  Appended as
            ``--analyzer.time_granularity <value>``.  Omitted when ``None``.
        analyzer_time_resolution (Optional[float]): Minimum time resolution for
            event timestamps.  Appended as
            ``--analyzer.time_resolution <value>``.  Omitted when ``None``.
        output_format (str): Output sink for analysis results.  Appended as
            ``--output=<value>``.  Supported values: ``"console"`` (default),
            ``"csv"``, ``"sqlite"``.
        output_compact (Optional[bool]): When ``True``, collapses the output
            into a compact representation.  Mapped to
            ``--output.compact=true|false``.  Omitted when ``None``.
        output_root_only (Optional[bool]): When ``True``, restricts output to
            the root-level aggregation only.  Appended as
            ``--output.root_only <value>``.  Omitted when ``None``.
        output_name (Optional[str]): Label or filename stem for the output
            artefact.  Appended as ``--output.name <value>``.  Ignored when
            ``None`` or whitespace-only.
        output_run_db_path (Optional[str]): Path to the run-level SQLite
            database used when ``output_format="sqlite"``.  Appended as
            ``--output.run_db_path <value>``.  Ignored when ``None`` or
            whitespace-only.
        cluster_type (str): Distributed cluster backend.  Appended as
            ``--cluster=<value>``.  Supported values: ``"local"`` (default),
            ``"slurm"``, ``"lsf"``, ``"pbs"``.
        cluster_n_workers (Optional[int]): Number of worker processes or nodes
            to allocate.  Appended as ``--cluster.n_workers <value>``.
            Omitted when ``None``.
        cluster_memory_limit (Optional[str]): Per-worker memory ceiling
            (e.g. ``"4GiB"``).  Appended as
            ``--cluster.memory_limit <value>``.  Omitted when ``None``.
        cluster_processes (Optional[int]): Number of processes per worker node
            (used by Dask-based backends).  Appended as
            ``--cluster.processes <value>``.  Omitted when ``None``.
        cluster_cores (Optional[int]): CPU cores allocated per job on batch
            schedulers.  Appended as ``--cluster.cores <value>``.
            Omitted when ``None``.
        cluster_memory (Optional[str]): Total memory per scheduler job
            (e.g. ``"16GiB"``).  Appended as ``--cluster.memory <value>``.
            Omitted when ``None``.

    Returns:
        List[str]: Ordered list of tokens forming the complete ``dfanalyzer``
        invocation.  The first element is always ``"dfanalyzer"``.  The list
        contains no shell meta-characters and is safe to pass directly to
        :func:`subprocess.run` with ``shell=False``.

    Edge cases:
        * ``view_types`` equal to the built-in default
          ``["file_name", "proc_name", "time_range"]`` is treated identically
          to ``None`` — no ``--view-type`` flags are emitted.
        * Boolean overrides (``analyzer_checkpoint``, ``analyzer_time_approximate``,
          ``output_compact``) are serialised as the literal strings ``"true"`` or
          ``"false"`` to match Hydra's expected format.
        * ``output_name`` and ``output_run_db_path`` are tested with
          :meth:`str.strip` before inclusion, so whitespace-only strings are
          silently dropped.
    """
    # dfanalyzer uses Hydra-style positional overrides (key=value), not GNU flags.
    cmd: List[str] = ["dfanalyzer"]

    if trace_path:
        cmd.append(f"trace_path={trace_path}")

    default_view_types = ["file_name", "proc_name", "time_range"]
    if view_types is not None and view_types != default_view_types:
        hydra_list = "[" + ",".join(view_types) + "]"
        cmd.append(f"view_types={hydra_list}")

    if debug:
        cmd.append("debug=True")
    if verbose:
        cmd.append("verbose=True")

    if analyzer != "dftracer":
        cmd.append(f"analyzer={analyzer}")
    cmd.append(f"analyzer/preset={analyzer_preset}")

    if analyzer_checkpoint is not None:
        cmd.append(f"analyzer.checkpoint={'True' if analyzer_checkpoint else 'False'}")
    if analyzer_checkpoint_dir is not None:
        cmd.append(f"analyzer.checkpoint_dir={analyzer_checkpoint_dir}")
    if analyzer_time_approximate is not None:
        cmd.append(f"analyzer.time_approximate={'True' if analyzer_time_approximate else 'False'}")
    if analyzer_time_granularity is not None:
        cmd.append(f"analyzer.time_granularity={analyzer_time_granularity}")
    if analyzer_time_resolution is not None:
        cmd.append(f"analyzer.time_resolution={analyzer_time_resolution}")

    cmd.append(f"output={output_format}")
    if output_compact is not None:
        cmd.append(f"output.compact={'True' if output_compact else 'False'}")
    if output_root_only is not None:
        cmd.append(f"output.root_only={output_root_only}")
    if output_name and output_name.strip():
        cmd.append(f"output.name={output_name}")
    if output_run_db_path and output_run_db_path.strip():
        cmd.append(f"output.run_db_path={output_run_db_path}")

    cmd.append(f"cluster={cluster_type}")
    if cluster_n_workers is not None:
        cmd.append(f"cluster.n_workers={cluster_n_workers}")
    if cluster_memory_limit is not None:
        cmd.append(f"cluster.memory_limit={cluster_memory_limit}")
    if cluster_processes is not None:
        cmd.append(f"cluster.processes={cluster_processes}")
    if cluster_cores is not None:
        cmd.append(f"cluster.cores={cluster_cores}")
    if cluster_memory is not None:
        cmd.append(f"cluster.memory={cluster_memory}")

    return cmd


class DFAnalyzerService(MCPService):
    """MCP service that wraps the ``dfanalyzer`` command-line executable.

    :class:`DFAnalyzerService` exposes two MCP tools on a single
    :class:`fastmcp.FastMCP` sub-server so that AI agents can trigger
    trace-file analysis and discover available configuration options without
    constructing raw shell commands.

    The service delegates all actual computation to the ``dfanalyzer`` binary
    (invoked via :mod:`subprocess`); it is purely a thin translation layer
    between the MCP protocol and the CLI.

    Attributes:
        analyzer_subservice (FastMCP): Sub-server named ``"DFAnalyzer"``.
            Hosts two MCP tools:

            * ``analyze`` — registered by :meth:`_register_analyze`.  Runs
              ``dfanalyzer`` with the caller-supplied configuration and returns
              its ``stdout`` on success, or a structured error message containing
              the exit code, ``stdout``, and ``stderr`` on failure.
            * ``list_presets`` — registered by :meth:`_register_list_presets`.
              Returns a static, human-readable reference card listing supported
              analyzer presets, cluster backends, output formats, and analyzer
              types.
    """

    def __init__(self) -> None:
        """Initialise the service and register all MCP tools on the sub-server.

        Side effects:
            * Creates ``self.analyzer_subservice`` (``FastMCP("DFAnalyzer")``)
              and registers the ``analyze`` tool via :meth:`_register_analyze`.
            * Registers the ``list_presets`` tool via
              :meth:`_register_list_presets` on the same sub-server.

        After ``__init__`` returns the sub-server is fully configured and ready
        to be mounted by the parent MCP gateway.
        """
        self.analyzer_subservice = FastMCP("DFAnalyzer")
        self._register_analyze()
        self._register_list_presets()

    def _register_analyze(self) -> None:
        """Register the ``analyze`` MCP tool on :attr:`analyzer_subservice`.

        The registered tool, ``analyze``, invokes the ``dfanalyzer`` binary with
        a fully-constructed argument list built by :func:`_hydra_args`.  It
        accepts the complete set of Hydra-style configuration knobs (trace path,
        view types, analyzer preset, cluster backend, output format, etc.) as
        typed keyword arguments, making the tool self-documenting to MCP clients
        that inspect tool schemas.

        Tool behaviour:
            * On success (exit code 0): returns the binary's ``stdout``, or the
              sentinel string ``"(no output)"`` if ``stdout`` is empty.
            * On failure (non-zero exit code): returns a structured error string
              containing the exit code, ``stdout``, and ``stderr`` so that the
              caller can diagnose the problem without shelling out manually.

        The inner ``analyze`` function is decorated with
        ``@self.analyzer_subservice.tool()`` to make it discoverable as an MCP
        tool named ``"analyze"``.
        """
        @self.analyzer_subservice.tool()
        def analyze(
            trace_path: str,
            view_types: Optional[List[str]] = None,
            debug: bool = False,
            verbose: bool = False,
            analyzer: str = "dftracer",
            analyzer_preset: str = "posix",
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
        ) -> str:
            """Run dfanalyzer on the provided trace path."""
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
            # exits on its own). subprocess.run() without a timeout blocks
            # forever waiting for the process to fully exit, not just for
            # output to stop, so a hung teardown looks like a stuck MCP
            # tool call even though the actual analysis already finished.
            # Give it a generous timeout, then treat a timeout as success
            # if it already produced console output — SIGKILL the hung
            # process and return whatever was captured before the kill.
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                stdout, stderr, returncode = result.stdout, result.stderr, result.returncode
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout or ""
                stderr = exc.stderr or ""
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

    def _register_list_presets(self) -> None:
        """Register the ``list_presets`` MCP tool on :attr:`analyzer_subservice`.

        The registered tool, ``list_presets``, requires no arguments and returns
        a static, newline-delimited reference card that summarises:

        * **Analyzer presets** (``analyzer/preset``): ``posix``, ``dlio``.
        * **Cluster types** (``cluster=``): ``local``, ``slurm``, ``lsf``,
          ``pbs``.
        * **Output formats** (``output=``): ``console``, ``csv``, ``sqlite``.
        * **Analyzer types** (``analyzer=``): ``dftracer``, ``darshan``,
          ``recorder``.

        The content is sourced from the official DFTracer/DFAnalyzer
        documentation and is intended as a quick-reference for AI agents
        selecting configuration values before calling ``analyze``.

        The inner ``list_presets`` function is decorated with
        ``@self.analyzer_subservice.tool()`` to make it discoverable as an MCP
        tool named ``"list_presets"``.
        """
        @self.analyzer_subservice.tool()
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
                "- posix (default)",
                "- dlio",
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

    def execute(self, data: dict) -> Optional[str]:
        """Compatibility entry-point required by the :class:`MCPService` abstract base.

        Constructs a ``dfanalyzer`` command string from *data* using
        :func:`_hydra_args` and returns it as a dry-run preview.  No subprocess
        is launched; the method exists solely to satisfy the
        :class:`~mcp_service_factory.MCPService` interface contract.

        Args:
            data (dict): Arbitrary key/value payload forwarded from the MCP
                gateway.  Recognised keys and their expected types mirror the
                parameters of :func:`_hydra_args`:

                * ``trace_path`` (str): Path to the trace file or directory.
                * ``view_types`` (List[str]): View-type identifiers.
                * ``debug`` (bool): Enable debug logging.
                * ``verbose`` (bool): Enable verbose output.
                * ``analyzer`` (str): Analyzer backend name.
                * ``analyzer_preset`` (str): Hydra preset name.
                * ``analyzer_checkpoint`` (bool | None): Checkpoint toggle.
                * ``analyzer_time_approximate`` (bool | None): Approximate time
                  toggle.
                * ``analyzer_time_granularity`` (float | None): Time bucket size.
                * ``analyzer_time_resolution`` (float | None): Time resolution.
                * ``output_format`` (str): Output sink identifier.
                * ``output_compact`` (bool | None): Compact output toggle.
                * ``output_root_only`` (bool | None): Root-only output toggle.
                * ``output_name`` (str | None): Output label or filename stem.
                * ``output_run_db_path`` (str | None): SQLite DB path.
                * ``cluster_type`` (str): Cluster backend identifier.
                * ``cluster_n_workers`` (int | None): Worker count.
                * ``cluster_memory_limit`` (str | None): Per-worker memory cap.
                * ``cluster_processes`` (int | None): Processes per worker.
                * ``cluster_cores`` (int | None): Cores per job.
                * ``cluster_memory`` (str | None): Total job memory.
                * ``command`` (any): Ignored.  Present for interface
                  compatibility; always excluded before passing *data* to
                  :func:`_hydra_args`.

        Returns:
            Optional[str]: A human-readable string of the form
            ``"Would run: dfanalyzer ..."`` showing the full command that
            *would* be executed.  Never returns ``None``.
        """
        cmd_string = " ".join(_hydra_args(**{k: v for k, v in data.items() if k != "command"}))
        return f"Would run: {cmd_string}"

    @property
    def name(self) -> str:
        """Unique service identifier used by :class:`MCPServiceFactory`.

        Returns:
            str: The string ``"dfanalyzer"``.
        """
        return "dfanalyzer"


MCPServiceFactory.register("dfanalyzer", DFAnalyzerService())


def run() -> None:
    """Run the standalone DFAnalyzer MCP server."""
    service = DFAnalyzerService()
    service.analyzer_subservice.run()


if __name__ == "__main__":
    run()
