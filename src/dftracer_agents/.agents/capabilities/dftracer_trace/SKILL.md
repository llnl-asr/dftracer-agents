---
name: dftracer_trace
description: Collect one run's dftracer traces - session_init_run (canonical per-run paths), session_snapshot_run_source (source + patch), session_run_with_dftracer (DFTRACER_ENABLE/INC_METADATA/LOG_FILE/DATA_DIR set, module preamble, flux-proxy wrapper, optional allocation-aware flux run), session_service_start/stop (the per-node dftracer_service daemon), session_wait_flux_job, session_analyze_traces (dftracer_info summary), session_search_papers_for_config (production configuration from papers + the app's repo) - and validate the collected run with dftracer_trace_check. Load at pipeline stage 4 (tracer), for PRELOAD/HYBRID runs, or on empty / zero-byte traces, "Code 2001: Data dirs not defined", missing POSIX/HDF5/MPI categories, "DFTRACER_ENABLE: No such file or directory" (signal 127) under flux run, "No running server found." from dftracer_service stop, 0-byte service_<host>.pfw.gz, or analysis numbers that look too small (1 process / ~1542 events).
---

# dftracer_trace

Converted from dftracer-agents' run/daemon tools, the `dftracer-tracer` agent and the `dftracer-preload-run`,
`dftracer-trace-utils` and `dftracer-smoke-test` (env vars) skills. You collect and split ONE run's traces, then stop.

## Rules
- Tool-first, in order: `session_init_run(run_id, run_name)` -> `session_snapshot_run_source` (own source tree; the
  source_path must live OUTSIDE `<run_name>/`) -> `session_service_start` -> `session_run_with_dftracer(run_id,
  command, run_name, data_dir="all", env_extra=...)` -> `session_service_stop` -> check sizes -> `session_split_traces`
  / `split` -> `event_count` -> `dftracer_trace_check(run_id, run_name, expected_ranks, require_compact=True)`. If the
  tools are missing, stop and ask for the dftracer MCP server; if they error, fix the tool before custom Bash.
- Env (set by `session_run_with_dftracer`; do not pass in env_extra without a reason): `DFTRACER_ENABLE=1`,
  `DFTRACER_INC_METADATA=1`, `DFTRACER_LOG_FILE=<WS>/<run_name>/traces/raw/<run_name>-<attempt>` (absolute, inside the
  workspace, never /tmp), `DFTRACER_DATA_DIR=all`, `DFTRACER_INIT=FUNCTION` for annotated binaries.
- HARD RULE - always `DFTRACER_DATA_DIR=all` (it is a path FILTER: anything narrower silently drops every POSIX/HDF5
  event outside it, leaving only C_APP events). If an old build rejects `all` with Code 2001, use `/`.
- HARD RULE - segregate each run's raw trace into its OWN subfolder `<WS>/.../traces/raw/<run_name>/` (user
  instruction 2026-08-02), mirroring `traces/compact/<run_name>/`; `mkdir -p` it before launching (a missing dir logs
  `unable to create log file ... errno=2` and gives a 0-byte trace). Essential with replicates and N-node probes.
- Forward env to every rank: `flux run` does NOT accept `-x` (it treats the name as the program: "DFTRACER_ENABLE: No
  such file or directory", signal 127) and env does not survive `flux proxy`. Wrap the run in a bash script that
  exports everything (modules, LD_LIBRARY_PATH, DFTRACER_*, LD_PRELOAD) and run `flux proxy <alloc> flux run -N <n> -n
  <t> --exclusive ./run.sh` (the tool writes such wrappers into `<WS>/tmp/`). `-x VAR` is mpirun syntax: with mpirun
  export each var AND pass `-x` for DFTRACER_ENABLE, DFTRACER_INIT, DFTRACER_DATA_DIR, DFTRACER_LOG_FILE,
  DFTRACER_INC_METADATA, LD_LIBRARY_PATH, LD_PRELOAD (omitting -x LD_PRELOAD = no interception; omitting
  DFTRACER_DATA_DIR = Code 2001 on every rank).
- Modes: FUNCTION (annotations, DFTRACER_C_INIT/FINI + START/END), PRELOAD (no source changes, LD_PRELOAD the
  preload library), HYBRID (only when the source has BOTH DFTRACER_C_INIT and DFTRACER_C_FINI; else PRELOAD). Values are
  case-sensitive uppercase (`hybrid` is ignored).
- Data on the system-detected PFS (Tuolumne `/p/lustre5/$USER/<app>/<run_name>`, create it first), never /tmp,
  /scratch (unless detected) or home. Traces stay in the workspace (the analysis tools read from there).
- Allocation-aware production runs: ASK for the active allocation id (or whether to spawn one with
  `flux batch -N <n> -q pdebug -t <mins>`); check it with `flux jobs -no "{id} {state} {t_remaining}" <JOBID>`; use ALL
  its nodes with `--exclusive` (48 processes per node, not 96, to avoid oversubscription); ~50% of node memory, >= 30
  min wall (DL: ~10 min of training; ask for a time budget, fix the epoch count), multi-GB checkpoints; baseline and
  optimization iterations are the same run class; never compare a smoke with production. Never block on a long
  `flux proxy` in the foreground (killing the client kills the job): background it and poll (`session_wait_flux_job`).
- Before a production run: `session_search_papers_for_config(run_id, app_name, problem_name)` (arXiv + Semantic
  Scholar + the app's GitHub repo; e.g. Flash-X `source/Simulation/SimulationMain/Sedov/extraParfiles/`
  `flash.bgp.2048.par`, `flash.bgp_bench.4096.par`, `sedov_io_*_32b_3d_weak.par`), read the paper for grid size, AMR
  levels, checkpoint cadence and scaling, and record citations + repo files in the report. Validation checklist: grid
  mode right (Paramesh `iProcs=jProcs=kProcs=1` with explicit nblockx/y/z), first checkpoint >= 500 MB (< 10 MB: grow
  the problem), >= 50 steps after 5 min, traces growing, binary NXB matches the par file.
- Replicates: every baseline or comparison run >= 5 replicates (`<run_name>/rep0..rep4`); CV > 10-15% -> 8, still high
  -> 10; report p50, p95, min, max. Smoke tests are exempt. Calibrate size smallest first (1 process -> 1 node -> ONE
  full-scale run in a 10-15 minute window) against wall time and `quota -v` before replicating; clean calibration output.
- Never substitute a smaller run for the requested one; before calling a multi-rank run impossible check: dataset too
  small for the rank count, a data-generation phase not run, queue contention (try the debug queue).
- Trace work only through the dftracer utils (view, event_count, reader, split, comparator, info, stats, diagnose,
  aggregator, call_tree, merge, index, plot) - never raw gzip/python/zcat on `.pfw`/`.pfw.gz`. ALWAYS split before
  analysing (raw per-rank dirs were silently truncated to 1542 events / 1 process vs ~99,666 / 8 ranks).
- Completeness gates, cheapest first: file count == rank count; zero-byte count == 0 (report "flushed N/total");
  trailing `{"cat":"dftracer","name":"end"}` on every app trace (read the last record with the reader/event tools),
  reported as "N/total end-terminated". A crash with every trace end-terminated was post-flush: the data is usable.
- Pin dftracer_service start, the app, and stop to the SAME hosts when the allocation is bigger than the run
  (`--requires=host:<a>,<b>,...`), check who else is running (`flux jobs -a ... | grep RUN`), and expect one non-empty
  `service_<host>.pfw.gz` per node (the service trace flushes only on stop).
- Attribute any crash before blaming the instrumentation: rerun the PRISTINE binary at the same scale and arguments.
- Never edit a shell script while it is executing (bash reads it by byte offset): copy to a new name.
- Capture the run record at the end: `session_capture_run_record(run_id, run_name, prev_run_name, source_path,
  run_script, run_log, param_files, notes)`.
- Return: raw + compact trace dirs, file count, total event count.

## Constraints
- Never `DFTRACER_DISABLE_IO`; never sudo; write only under the session workspace (and the PFS data dir).
- Logs under `<WS>/artifacts/`. Propose lessons to the main thread, redacted.
- An allocation may be shared with another live session: never assume it is all yours.

## Corner cases
- Fortran programs (no C main): FUNCTION may give empty traces; PRELOAD with `DFTRACER_DATA_DIR=all`,
  `LD_PRELOAD=<path>/libdftracer_core.so.<version>` (the real core, not the Python wrapper), CCE runtime libs on
  LD_LIBRARY_PATH; still empty -> `ldd <binary> | grep dftracer`, log-path permissions, DFTRACER_ENABLE on every rank.
- 0-byte traces despite DFTRACER_ENABLE=1 -> init without finalize (try/finally around finalize, mpi4py atexit order).
- Expected categories: POSIX, STDIO (DATA_DIR matches), HDF5 (dftracer built/composed with HDF5), MPI (built with MPI
  and the runtime MPI matches the compile-time one: `strings libdftracer_core.so | grep include`), dftracer metadata;
  PR/HH/FH metadata. FH entries must name the real data files, not only the run dir.
- Common errors: no trace files -> DFTRACER_INIT not uppercase or not forwarded; empty files -> no I/O under DATA_DIR;
  HDF5 missing -> not built with HDF5; MPI missing -> header/runtime mismatch; ranks crash -> LD_PRELOAD not forwarded.
- `dftracer_view`: an empty result with `scanned=0 skipped=N` means an empty index, not absent data - use the reader
  (1-based lines; an `end` past the true count returns 0 lines: bisect the count) and match `event_count`. Compound
  queries with parentheses/`or` silently match nothing; quoted DSL strings lose their quotes through `flux proxy` (use
  presets, reader or event_count). `reader(mode="lines")` rejects start=0.
- `comparator` returns 0 matched events on Python FUNCTION-mode traces (integer `ph`): check event_count first.
- `analyze()` with cluster_n_workers=32 on a small trace exhausts `ulimit -u` ("Resource temporarily unavailable"):
  scale workers to trace size (4-8, or 1). dfanalyzer's "Trace Count" differs from event_count's "Valid Events":
  event_count is the ground truth.
- `session_analyze_traces(query_type=...)` accepts only `summary` or `detailed` (anything else now errors).
- `gzip -t` "unexpected end of file" = the writer died before the trailer: tail lost, not copy corruption (compare
  sizes with the source before deleting it).
- Pegasus/PMC: always `pegasus-plan --conf <properties>` (auto-discovery falls back to DAGMan) and size PMC `-n` to the
  DAG's task parallelism.
- Measure write BYTES per rank (args.ret over POSIX write/pwrite by pid), not write calls, to detect a single writer.
- `session_service_stop` returns ok even on a non-zero stop (the daemon may have exited): check the service traces.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
For each failed check of `dftracer_trace_check`:
- `run_recorded` -> `session_init_run` then `session_run_with_dftracer` with this run_name.
- `run_succeeded` -> rerun the pristine binary to attribute; report the real status; never substitute a smaller run.
- `traces_present` -> DFTRACER_ENABLE reached every rank (wrapper script), LOG_FILE dir exists, DATA_DIR=all, binary
  links dftracer or LD_PRELOAD set.
- `no_zero_byte` -> ranks that never finalized: check teardown crashes (rerun pristine), finalize ordering.
- `single_attempt` -> delete stale traces of earlier attempts (the tool clears raw/ itself; hand-launched runs do not).
- `rank_count` -> env not forwarded / ranks died before init.
- `compacted` -> `session_split_traces(run_id, run_name)`.
- `service_stopped` / `service_traces` -> `session_service_stop`; pin start/app/stop to the same hosts.
Then predict (re-run) and validate again; the same failure twice stops for the user.

## Self-heal
- A run tool that builds a wrong command (flux wrapper, env, preamble) is fixed here (capability_update) and the
  server restarted; no hand-rolled launches meanwhile.

## Self-learn
- What is explored here becomes a capability: an app's production configuration and run shape become
  `workload_<app>`, an allocation/launcher/PFS quirk `system_<machine>`, a trace-utils bug `tools_dftracer_utils`. See
  capability_builder.

## What works
- Flash-X Sedov 3D, 768 ranks: FUNCTION + DFTRACER_DATA_DIR=all + an existing LOG_FILE dir -> ~284k events with HDF5
  categories; production runs through a bash wrapper under `flux proxy <alloc> flux run -N <n> -n <n*48> --exclusive`.
- 2026-07-08: `dftracer_split -d <raw> --output <split> --index-dir <split>/idx --compress --app-name <run>` before
  dfanalyzer -> 98,695 events / 8 processes / 2 nodes (raw gave 1542 / 1).
- miniFE sweep: 16/16 traces per run, all 96 end-terminated although the app SIGABRTed at teardown (post-flush).
- Daemon bracketing: `session_service_start` -> `session_run_with_dftracer` -> `session_service_stop`, hosts pinned
  with `--requires=host:...` inside a larger allocation.
