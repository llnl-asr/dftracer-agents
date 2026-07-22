## Pitfalls

- A small event count can mean broken tracing, not a fast app.
- A single hot function can skew the analyzer summary.
- Comparing runs with different chunk shapes can mislead the comparator.
- **`analyze()`/`diagnose()` can silently under-count a trace.** Confirmed on a
  16-rank VPIC best_case trace (2026-07-14): `analyze()` reported 527,206
  events / 4 processes across two separate reruns, while the ground truth
  (`event_count` MCP tool + the actual 16 `.pfw.gz` rank files on disk) was
  2,111,806 events / 16 ranks. This is a distinct failure mode from the
  previously-documented run-to-run non-determinism — it was consistent across
  reruns, just consistently wrong. Always cross-check `analyze()`'s reported
  process/rank count against `event_count()` or a directory listing of the
  raw trace files before trusting its aggregate numbers; if they disagree,
  fall back to manual event-level aggregation (e.g. decompress `.pfw.gz` and
  aggregate `dur` by function `name`/`cat` in Python) rather than reporting
  the tool's numbers as-is.
- **Root cause confirmed 2026-07-22 for one instance of the above under-count
  class: invoking the `dfanalyzer` CLI directly (e.g. to pass a raw Hydra
  override like `analyzer.preset.async_layers=[...]` that the MCP `analyze`
  tool's schema doesn't expose) bypasses the tool's own pre-processing.**
  Direct CLI invocation against a compact trace directory discovered only 6
  of 48 known processes and silently missed entire categories (`compute`,
  `preprocess`, `communication_except_io`, `stdio` never appeared) even
  though the SAME directory analyzes correctly (48 procs, all categories)
  through the normal `mcp__dftracer__analyze` MCP call. The MCP tool's
  `_ensure_analyzable_path` does real work (split/index) before invoking
  `dfanalyzer` that a bare CLI call skips. **Fix:** never hand-invoke
  `dfanalyzer` directly against a compact/raw trace directory, even to reach
  a Hydra field the MCP tool doesn't expose — either extend the MCP tool
  with the override you need (see the matching MCP-tool-gap note in
  `dftracer-optimization-kb`/memory) or manually replicate
  `_ensure_analyzable_path`'s split+index steps first if a code change isn't
  possible in the moment.
- **`async_layers` (dfanalyzer's built-in compute/data/comm overlap metric —
  see the "Rules" section above) is hardcoded to diff every listed layer
  against `compute_time_proc` specifically, not an arbitrary pair.** For a
  pairwise question where NEITHER side is `compute` (e.g. "does data overlap
  with communication"), this mechanism doesn't answer it directly. Exact
  event-level overlap (per-rank interval-merge/sweep-line over raw `ts`/`dur`
  from the compact trace) is the reliable fallback for any pair, compute or
  not, and is EXACT rather than a bound — prefer it over the bucket-level
  `min(A,B)`/`max(0,A+B-bucket)` bound whenever raw event timestamps are
  reachable (they always are, via the sanctioned trace-reading tools).
- **`diagnose()` can score "critical" on statistically meaningless absolute
  counts.** Same VPIC session: it scored `posix_close_count_sum=1.0` as
  "critical" — a percentile-based score on a metric with fewer than 5 total
  operations. Always check the absolute count/magnitude behind a severity
  score before treating it as a real bottleneck, especially for POSIX/IO
  metrics on a workload that does little or no file I/O.
- **A function-level `comp=` annotation tag is not proof of what a function
  actually spends its time doing.** VPIC's `dump_energies` was annotated
  `comp="io"` (its name suggests an I/O dump routine) but trace-level
  interval-containment analysis showed 99.7% of its time is inside
  `MPI_Allreduce` — it is a communication routine, not an I/O routine. When
  diagnosing a FUNCTION-mode-annotated app, verify actual time attribution
  via nesting/containment analysis (which child spans dominate a parent
  function's wall time) rather than trusting the `comp=` label alone,
  especially before recommending an I/O-side fix.
- **`diagnose()` can report "0 metric observations across 0 view(s)" against
  a checkpoint that clearly has scoreable data.** Reproduced twice on a
  128-rank VPIC 8-node trace (2026-07-14): `analyze()` with
  `analyzer.checkpoint=True` printed a normal console summary (340.85M POSIX
  ops, 325.0 MB, 196.9 MB/s bandwidth) and left `_flat_view_*.parquet` files
  on disk, but the immediately-following `diagnose()` call against that same
  `checkpoint_dir` returned zero observations/views scored both on a
  near-empty (no-I/O) trace and on the data-rich I/O-enabled rerun.
  **ROOT-CAUSED AND FIXED 2026-07-20** (PECAN/PDBspheres session): two
  compounding causes, both now fixed at the tool level —
  1. The `posix`/`dlio` analyzer presets silently return EMPTY flat views
     (0 rows, no error, `returncode=0`) whenever the trace's `cat` values
     don't match either preset's hardcoded layer-name list — e.g. an app
     using `dft_event_logging` with custom category strings
     (`"compute"`/`"preprocess"`/`"communication-io"`) traces `cat=hdf5` +
     those custom strings, none of which are `"POSIX"` or any DLIO-benchmark
     layer name. The underlying raw high-level-metrics
     (`_hlm_*_time_range_*/part.*.parquet`) had all the real per-`cat`/
     `func_name` data the whole time — only the preset-specific "views"
     aggregation discarded it. **Fix: use `analyzer_preset="generic"`**
     (`dftracer-analyzer` package, `AnalyzerPresetConfigGeneric`,
     `auto_layers_by_category=True`) — this is now the MCP `analyze` tool's
     default. See the two-stage rule above.
  2. The MCP `diagnose` tool's `_diagnose_via_api` always returned `None`
     because it checked `hasattr(diagnoser, "diagnose_checkpoint")` against
     an OLD `dfdiagnoser` package release that didn't have that method yet —
     it always silently fell through to a naive pandas per-column-percentile
     fallback that (separately) had a bug swallowing all exceptions. Fixed:
     `dfdiagnoser` upgraded to a release with `Diagnoser.diagnose_checkpoint`
     (dynamic `<layer>_ops_slope`-based layer discovery, so it works for any
     preset including `generic`), and `_diagnose_via_api` now actually calls
     it and returns real `DiagnosisFinding` objects (motif/severity/
     confidence/recommendation) instead of silently no-op-ing.
  If you still see "0 observations" after these fixes, THEN fall back to
  reading the analyzer's own console summary (Time Period Summary / Layer
  Breakdown) directly — but try the generic preset + updated diagnose first.
- **`diagnose_checkpoint()` can crash with `ValueError: signal only works in
  main thread of the main interpreter`, but only the FIRST time it's called
  in an MCP-server process.** Root cause (found 2026-07-20, same PECAN
  session): `Diagnoser.diagnose_checkpoint` imports
  `dftracer.analyzer.fact_engine`, which imports `dftracer.analyzer.cluster`,
  which imports `dask_jobqueue` — and `dask_jobqueue/runner.py` calls
  `signal.signal(signal.SIGINT, ...)` at MODULE IMPORT TIME (an upstream
  side-effect, not a dftracer/dfdiagnoser bug per se). `signal.signal` only
  works on the main thread; FastMCP tool calls run on a worker thread, so the
  first call in the process — whichever tool happens to trigger this import
  chain first — crashes. Because Python caches imported modules, every
  subsequent call in the SAME process would have succeeded fine (it's a
  one-shot, first-import-only failure). Fixed by eagerly importing
  `dftracer.analyzer.fact_engine` at MODULE LOAD time in
  `dfdiagnoser_service.py` (i.e. on the MCP server's main thread, at server
  startup) so the crash-prone import happens safely before any worker-thread
  tool call can trigger it lazily.
