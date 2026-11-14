---
name: dftracer_analyze
description: Analysis stage of the dftracer pipeline - run dfanalyzer (analyze, generic preset first) and the dftracer-utils trace tools (index, event_count, reader, info, view, stats, aggregator, comparator, split, MPI variants) over a run's compact traces, compare two runs, and measure compute/I-O/communication overlap correctly across processes; validate with dftracer_analyze_check. Load to analyze or compare traces, or on "Failed to open RocksDB ... .dftindex/*.log: No such file or directory", an EMPTY Layer Breakdown with returncode 0 (posix/dlio preset on custom dft_event_logging categories), analyze() reporting fewer processes than ranks ran, no u_<layer>_time_proc overlap columns (async_layers empty), "Total Files: 0", dfanalyzer hanging after "Cluster teardown", "unrecognized arguments: --trace-path", a stale event count after re-splitting, or an overlap percentage that is 0% or above 100%.
---
Cross-references: [[dftracer_diagnose]], [[dftracer_optimize]], [[dftracer_opt_kb]], [[dftracer_papers]], [[dftracer_optimize_io]], [[dftracer_optimize_compute]], [[dftracer_optimize_communication]], [[dftracer_optimize_memory]]

# dftracer_analyze

Pipeline stage 5: analyze compacted traces with dfanalyzer and the dftracer-utils tools, sanity-check trace
quality first, compare two runs when given, and hand a ranked bottleneck list (canonical order I/O ->
communication -> memory -> compute) with trace-quality caveats to [[dftracer_diagnose]] and
[[dftracer_optimize]]. This stage analyzes and reports; it never applies fixes.

## Rules
- Load the plan section first: `session_read_file(run_id, subfolder=".", filepath="pipeline_plan.md")`, find `## STEP N: dftracer-analyzer` and follow it verbatim; if it is missing or contradicts the dispatch inputs, report back instead of guessing.
- Tool-first, always, in this order: `analyze` (dfanalyzer), `diagnose` (see [[dftracer_diagnose]]), `comparator` (two runs), `event_count`, `reader`, `session_analyze_traces` (session-scoped, lives in the session tools). If the tools are not available, stop and ask for the dftracer MCP server; if they error, fix the tool or its wiring before any custom Bash/Python.
- Never read traces by decompressing them yourself (no gzip/zcat/grep/json over `.pfw.gz`): use `view` (query DSL, `output_file=` for large pulls), `aggregator`, `stats`, `reader`, `event_count`, or dftracer-utils' TraceViewer.
- Separate findings in the report: a table of TOOL FINDINGS (dfanalyzer, dfdiagnoser, comparator, ...) and MANUAL ANALYSIS (only where tools failed), each finding labelled with its source. Never conflate them.
- Step 1, trace quality FIRST: compare `event_count` and the unique-file count against expectations. A tiny unique-file count with a huge event count means one hot annotated function dominates the trace: flag it and name the function (group the largest trace by `name` with `aggregator`/`view`) instead of trusting the numbers.
- Step 2, index before analyzing: `index(directory=<compact>, force=True, executor_threads=1)` on any trace directory not analyzed before. Concurrent analyze workers building the RocksDB index at once race and crash (`Failed to open RocksDB ... .dftindex/*.log: No such file or directory`); built once, every worker only reads it. analyze()'s `_ensure_analyzable_path` does this itself only when it has to split raw per-rank traces, not for an already-compact/split directory.
- Step 3, two-stage analysis, generic first: `analyze(analyzer_preset="generic")` (the tool's default) FIRST, unconditionally, with checkpoint enabled. It auto-discovers one layer per distinct `cat` present and works for any app, including custom `dft_event_logging` categories ("compute", "preprocess", "communication-io"). Only after it, and only when you need their semantic layer breakdown (raw POSIX call classification, DLIO phase timing), run `posix` or `dlio`.
- Step 4, `diagnose` on the checkpoint ([[dftracer_diagnose]]); it works against any preset's checkpoint.
- Step 5, two runs: `comparator(baseline=..., variant=...)`; it matches by chunk index, so flag when parallelism or chunk counts differ between the runs.
- Cross-check every `analyze()` summary (process count, event count) against `event_count` and the known rank/pid count before trusting aggregates.
- Rank by TIME, not event count: aggregate `dur` per category/op (`aggregator`) before ranking.
- Verify `comp=` labels by containment analysis before recommending a fix for the category a label implies (see Corner cases).
- Run trace-processing tools on compute nodes inside a flux allocation, never the login node: pass `allocation_id=<jobid>` (reuse the session's live allocation, else ask the main thread/user for a small 1-node one). Small checks on a tiny trace need not wait for an allocation.
- Pipeline eagerly: analyze the units (workloads, replicates) that are ready now, track pending ones separately, and say in every report which units are included and which are pending.
- Bracket the step with `profile_step_begin(step="STEP N: dftracer-analyzer", agent="dftracer-analyzer")` / `profile_step_end(...)`; on retry close with status="failed" and the real error and reopen with the SAME step string; never `profile_bind`; never status="ok" for a step that did not succeed.
- Locate before reading: `graph_query(mode="docs", question=...)` for skill sections, `graph_query`/`graph_ensure` for code; open only the file:line it names.
- Last step: `dftracer_analyze_check(trace_dir=..., checkpoint_dir=..., baseline=..., variant=..., analysis_output=..., expected_processes=<ranks that ran>)` must return status "pass" before the numbers are reported.

## Constraints
- Read only the active run's trace directories (`traces/raw/`, `traces/compact/`) and metadata; never patch source from this stage; never apply optimizations.
- Never hand-invoke the `dfanalyzer` CLI against a compact/raw trace directory, even to reach a Hydra field the `analyze` tool does not expose: it skips `_ensure_analyzable_path`'s split+index and silently under-counts. Extend the tool with the override instead, or replicate its split+index steps first if a code change is impossible right now.
- dfanalyzer is a Hydra CLI: positional `key=value` overrides (`trace_path=<p>`, `view_types=[file_name,proc_name,time_range]`, `analyzer/preset=posix`, `analyzer.checkpoint=True`, `analyzer.checkpoint_dir=<p>`, `output=console`, `cluster=local`), never GNU `--flag value`.
- If an MCP tool fix needs a server restart, stop and report the tool you need; never reproduce its job by hand.
- Logs go under `<WS>/artifacts/<step>_<what>.log`, never only the terminal and never `<WS>/tmp/`.
- PFS rule: the analyzed run must have written its data to the system-detected PFS; flag traces whose `FH` entries point only at `/tmp` or home as invalid for benchmarking.
- Redact before persisting anything shared: no usernames, absolute user paths, emails, flux job ids, session UUIDs or hostnames.

## Corner cases
- posix/dlio presets: a SILENT `returncode=0` with an EMPTY Layer Breakdown and zero-row flat views when the trace's `cat` values do not match their fixed layer-name list (PECAN/PDBspheres: 130M-148M real events discarded while reporting success). The raw `_hlm_*_time_range_*/part.*.parquet` had the data; only the preset view aggregation dropped it. Use generic.
- Silent under-count: VPIC 16-rank best_case (2026-07-14) analyze reported 527,206 events / 4 processes on two reruns vs ground truth 2,111,806 events / 16 ranks (`event_count` + the 16 rank files). Consistent, just wrong. Cross-check and, if they disagree, aggregate `dur` by `name`/`cat` with `aggregator`/`view` (TraceViewer) and say so.
- Non-determinism: analyze() on identical input gave 14.35 s / 265k events / 13 procs once and 86.47 s / 607k / 37 procs the next (trace known to have 925,828 events, 64 pids). Rerun once; if still inconsistent, aggregate with `view`/`aggregator` and label which numbers came from tools and which from that aggregation. A checkpoint from such a run makes diagnose() score nothing; never report "no bottlenecks" from it.
- Direct CLI run (2026-07-22): bare `dfanalyzer` with `analyzer.preset.async_layers=[...]` found 6 of 48 processes and missed `compute`, `preprocess`, `communication_except_io`, `stdio`; the same directory through the `analyze` tool gave 48 procs and every category.
- Event counts mislead: STDIO 166,952 events cost 1.50 s (~9 us/op, ~11-byte writes) while POSIX 670,158 events cost 65.5 s.
- `comp=` label is the annotator's guess from the name: vpic-kokkos `dump_energies` was `comp="io"` but 99.7% of its time was inside `MPI_Allreduce`. For FUNCTION-mode apps, for each top-level annotated function sum its nested child spans (`[ts, ts+dur]` inside the parent's) by category/name and see which dominates; "dump"/"write"/"checkpoint" names are the most common mislabels.
- dfanalyzer's dask LocalCluster can hang at teardown after printing all output (process at ~99% CPU after "Cluster teardown"). The `analyze` tool now runs with no timeout (a fixed bound killed legitimate long analyses of 26M+ events); it decodes TimeoutExpired bytes defensively. If driving dfanalyzer via Bash, background it, read the log, and kill that exact PID once "Cluster teardown" appears; never `wait` on it.
- The hang is not only a large-trace problem: RAJAPerf (2026-08-05) timed out twice on an 8.6 MB / ~47,588-event compact trace with NO stdout captured, which suggests a hang in the analysis phase itself, not teardown. Unresolved: investigate the analysis phase for that trace shape/preset if it recurs.
- `session_analyze_traces` on folder-segregated layouts (`<run_name>/traces/compact/<workload>/<rep>/`): the compact root only holds subdirectories and fails with "No files found"; pass `subpath="<workload>/<rep>"` (e.g. "write/rep1").
- Stale index: after re-splitting, `session_analyze_traces` kept reporting the old count (391 events from 1 file) because `traces_split/idx/` is cached. Remove `<ws>/traces_split/idx/`, re-split with force=True, then re-analyze.
- "Total Files: 0" and no POSIX events although C_APP annotations record: `DFTRACER_INIT=0` disables the constructor and the POSIX interceptor. Leave DFTRACER_INIT unset (FUNCTION) or PRELOAD/HYBRID as appropriate, never 0 when POSIX tracing is wanted; fix collection before analysis.
- comparator matches by chunk index: runs with different chunk shapes or parallelism mislead it.
- A small event count can mean broken tracing, not a fast app; a single hot function can skew the summary.

## Overlap analysis: dfanalyzer's built-in async_layers metric
Compute/data/comm overlap ("is X overlapped with compute", "how much is compute stalled on Y") is a built-in
dfanalyzer metric; do not hand-derive it from flat-view bucket sums. `dftracer/analyzer/metrics.py` computes,
for every layer in the preset's `async_layers` list, `u_<layer>_time_proc = (layer_time_proc - compute_time_proc).clip(lower=0)`
per bucket/process, emitted as `u_<layer>_time_proc`, `u_<layer>_time_proc_frac_self`, `u_<layer>_time_proc_frac_total`.
The catch: `async_layers` defaults to `[]` for the base preset and for `generic` (only DLIO ships a list, with
DLIO's own layer names), so with an empty list the metric silently never runs and no `u_` columns appear. Fix:
pass an explicit override naming the app's own layers, e.g. `async_layers=[communication_io]` or
`async_layers=[communication_io, communication_except_io]`, through the `analyze` tool (extend the tool if its
schema lacks it; never the bare CLI, see Constraints), and read the `u_` columns. It is hardcoded to diff against
`compute_time_proc`: for a non-compute pair (data vs communication) reason from both layers' `u_*_vs_compute`
numbers, or use exact event-level overlap (below); the bucket bounds `min(A_time, B_time)` (upper) and
`max(0, A_time + B_time - bucket_duration)` (lower) are the last resort.

## Overlap analysis across processes: the dynamic-interval, event-splitting algorithm
Use for any "does X overlap with Y" / data-stall / pipeline-overlap question across multiple processes.
Pull events with `view`, never by hand-parsing `.pfw.gz`, e.g.
`view(directory="<compact_trace_dir>", query='cat == "compute" OR cat == "communication-io"', output_file="<scratch>/events.ndjson")`
(bloom-filter pruned, fast even on traces with tens of millions of other events), then load the NDJSON.

Two wrong ways:
1. Same-pid sweep-line interval merge for cross-process pipelines: compute and data-loading annotated on the
   main loop's own timeline never intersect on that pid (a single-threaded loop is serial), giving a spurious 0%
   that says nothing about DataLoader workers. PECAN (2026-07-22): 0.00 s before AND after a real prefetch fix
   that improved wall time.
2. Bucket bound `max(0, busyA + busyB - bucket_width)` with a bucket narrower than event durations and no
   splitting: an event longer than the bucket is attributed to one bucket, busy time exceeds the width, and the
   result is impossible (PECAN: p99-sized buckets without splitting gave overlap_pct = 204.75%).

Correct algorithm (validated 2026-07-22, PECAN baseline5):
1. Pull all `(pid, cat, ts, dur)` events of catA and catB across ALL processes via `view`.
2. Bucket width: `2 * max(dur)` is always correct but can collapse resolution (one 64.2 s outlier gave a 128.4 s
   width against a 144 s job: 2 buckets). Prefer a percentile (p99 worked) for resolution, ONLY with step 3.
3. If using less than the true max: split every event crossing a bucket boundary into per-bucket partial
   durations, so every per-process per-bucket busy time is bounded by the width.
4. Per bucket, per category: `busyA(bucket) = max over pid of the summed catA durations of that pid in that bucket`
   (not the sum across processes, not a single pid); same for busyB.
5. Per bucket: `overlap = max(0, busyA + busyB - bucket_width)`; `total_overlap = sum over buckets`.
6. Report `overlap_pct_of_A = total_overlap / total_A * 100`, `stall_pct_of_A = 100 - overlap_pct_of_A`, and as
   cross-checks `overlap_pct_of_B` and `total_overlap / min(total_A, total_B) * 100`. None may exceed 100; if one
   does, the splitting step is wrong.

This is deterministic logic that belongs in a tool, not a scratch script: dfanalyzer's `async_layers` is related
but uses fixed buckets with no dynamic sizing or splitting. If it is needed repeatedly, propose (with user
confirmation) extending `dftracer/analyzer/metrics.py` or a new MCP tool with this algorithm.

Known gap, per-worker traces under `multiprocessing_context="spawn"`: dftracer names each process's raw trace
`<run_name>-<hash>-<app_name>.pfw.gz`; the main rank gives `-app.pfw.gz`, a DataLoader worker (no registered app
name) gives `<run_name>-<hash>-.pfw.gz` in the same flat `traces/raw/` directory (not a `worker_N` subdirectory).
PECAN (2026-07-22): `baseline5` (fork, num_workers=2, 16 ranks) had 16 `-app` + 64 unnamed worker files;
`io_opt4` (spawn, num_workers=6, 16 ranks) had 16 `-app` and ZERO unnamed files: the 96 spawned workers produced
no trace at all. This blocks step 4 (needs the workers' events). Before using the algorithm on a spawn-context
trace, count unnamed files: `find <raw_trace_dir> -iname '*.pfw.gz' -not -iname '*-app.pfw.gz'`; if 0, the overlap
cannot be computed and a spawn-based win is an unverified mechanism. The documented Hybrid-mode fix did NOT
close it: `DFTRACER_INIT=HYBRID` is not a value this dftracer's `ProfileInitType` knows (only
PROFILER_INIT_FUNCTION / PROFILER_INIT_LD_PRELOAD) and silently no-ops; `LD_PRELOAD=libdftracer_preload.so`
alone also gave zero worker traces and hung at teardown (it also surfaced a separate libstdc++ RPATH bug in
`libdftracer_preload.so`). Status: open. Untried next steps: strace/ltrace a live spawned worker to see whether
the preload constructor fires; check whether `DFTRACER_LOG_FILE`'s hash-based naming collides between parent and
children; `DFTRACER_LOG_LEVEL=DEBUG` for an explicit skip in a spawned child. The PECAN -35.7% num_workers=6 /
spawn win is measured from the app's own epoch timer, so it stands; its overlap mechanism is unvalidated.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
Each failed check of `dftracer_analyze_check` -> repair, then run the predictor again and validate again; the same failure twice stops the loop for the user.
- trace_dir_exists / baseline_exists / variant_exists: wrong path; resolve the run's `traces/compact/` (session_get_run_paths) and pass it.
- trace_dir_has_traces / baseline_has_traces / variant_has_traces: the run produced no trace; send it back to the tracing stage (check DFTRACER_ENABLE, DFTRACER_INIT not 0, DFTRACER_DATA_DIR=all, the service daemon) before analysis.
- trace_dir_no_empty_traces / baseline_no_empty_traces / variant_no_empty_traces: a rank or worker did not finalize (e.g. 0-byte DataLoader worker files); re-run or drop the rank, then split/compact again.
- checkpoint_flat_views: re-run `analyze` with `analyzer.checkpoint=True` and `analyzer.checkpoint_dir=<dir>`, generic preset first; zero-row views from posix/dlio mean use generic.
- checkpoint_raw_stats: the checkpoint is incomplete (killed mid-run or teardown hang); delete it and re-run analyze.
- comparison_has_both_runs: give both the baseline and the variant (optimized) run's compact trace directories.
- comparison_runs_distinct: the variant points at the baseline; pass the optimized run's own (isolated, re-split) trace directory.
- analysis_output_ok: read stderr in the output; index the directory first (force, 1 thread), use the generic preset, fix the Hydra arguments; "(no output)" after a hang means re-run on an allocation.
- process_count_matches: analyze under-counted; index (force, executor_threads=1) and re-run through the `analyze` tool (never the bare CLI), cross-check with `event_count`; if it stays wrong, aggregate with `aggregator`/`view` and label those numbers as manual analysis.

## Self-heal
- If a tool here is wrong, fix the tool (capability_update), never work around it in shell; if the fix needs a server restart, stop and say which tool.
- Before treating "0 observations"/wrong counts as a code bug, check for a stale MCP server process (see [[dftracer_diagnose]] Corner cases).

## Self-learn
- What is explored here becomes a capability: see capability_builder. A new pitfall or working recipe is proposed to the main thread with the user's confirmation before it is persisted (target -> symptom -> root cause -> exact content); app-specific caveats go to workload_<app>/software_<app>, site quirks to system_<x>, library details to software_<lib>.
- Every user answer or correction is a lesson; deterministic, repeated analysis logic (the overlap algorithm) becomes a tool.

## What works
- 2026-07-20 tool:analyze: `index(directory=<compact>, force=True, executor_threads=1)`, then `analyze(analyzer_preset="generic", checkpoint enabled)`, then diagnose on the checkpoint: works for custom-category apps where posix/dlio silently returned nothing.
- 2026-07-22 tool:view (overlap): PECAN baseline5 compute vs data-loading, p99_dur = 0.412 s -> width 0.824 s -> 169 buckets (2 with true-max sizing); total_A(compute) = 114.14 s, total_B(comm-io) = 106.28 s, total_overlap = 81.92 s -> overlap_pct_of_compute = 71.8%, compute stall ~28.2% (superseded the false same-pid 0%).
- 2026-06-24 tool:analyze: Hydra positional overrides (`trace_path=... analyzer/preset=posix analyzer.checkpoint=True analyzer.checkpoint_dir=... output=console cluster=local`) are what dfanalyzer accepts; the tool generates them.
- 2026-07-10 tool:session_analyze_traces: `subpath="<workload>/<rep>"` for folder-segregated compact layouts.
