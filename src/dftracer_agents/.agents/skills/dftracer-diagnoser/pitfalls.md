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
  near-empty (no-I/O) trace and on the data-rich I/O-enabled rerun. The
  diagnoser isn't reading the flat-view parquet the analyzer just wrote in
  at least some configurations. Don't block a report on `diagnose()`
  returning a severity table — read the analyzer's own console summary
  numbers directly (Time Period Summary / Layer Breakdown) and reason about
  bottleneck severity from those instead when `diagnose()` comes back empty
  against a checkpoint you know has data.
