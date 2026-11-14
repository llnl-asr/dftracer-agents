---
name: dftracer_diagnose
description: Diagnosis stage of the dftracer pipeline - score dfanalyzer checkpoints with DFDiagnoser (diagnose, session_diagnose_bottlenecks -> diagnosis.json) into a ranked high/critical bottleneck list, map symptoms to likely causes against the optimization rules, and validate with dftracer_diagnose_check. Load after analysis and before optimizing, or on "traces_split not found - run session_split_traces first", "dfanalyzer ran but produced no _flat_view_*.parquet", "DFDiagnoser did not run successfully", "No module named dfdiagnoser", "0 metric observations across 0 view(s)", "ValueError: signal only works in main thread of the main interpreter", a "critical" score on a metric with a handful of operations, or a diagnosis that disagrees with event_count.
---
Cross-references: [[dftracer_analyze]], [[dftracer_optimize]], [[dftracer_opt_kb]], [[dftracer_papers]], [[dftracer_optimize_io]], [[dftracer_optimize_compute]], [[dftracer_optimize_communication]], [[dftracer_optimize_memory]]

# dftracer_diagnose

Diagnose one session at a time: turn trace evidence into a ranked bottleneck list with caveats, compare
against the optimization rules before proposing a cause, and report the smallest reliable fix path. This stage
never changes source code and never applies optimizations.

## Rules
- Load the plan section first: `session_read_file(run_id, subfolder=".", filepath="pipeline_plan.md")`, find `## STEP N: dftracer-diagnoser` and execute it verbatim; report contradictions instead of guessing.
- Tool-first, in this order: `diagnose` (dfdiagnoser), `analyze` (context, [[dftracer_analyze]]), `comparator` (baseline vs optimized), `event_count`, `reader`, `session_analyze_traces`. If the tools are missing, stop and ask for the dftracer MCP server; if they error, fix the tool or its wiring before custom Bash/Python.
- Separate TOOL FINDINGS (dfdiagnoser, dfanalyzer, comparator) from MANUAL ANALYSIS (only where tools failed) in a table; label every finding with its source.
- Verify trace quality before diagnosis: sanity-check trace size and event counts (`event_count`) first. If the trace is too small or malformed, fix collection first.
- Use `event_count` and `comparator` when available. Prefer ranked bottlenecks over narrative guesses; keep the diagnosis grounded in trace evidence and the optimization skills ([[dftracer_optimize_io]] and siblings), and check `opt_kb_lookup` ([[dftracer_opt_kb]]) for what is already known on this system/workload.
- Index before analyzing (`index(directory=<compact>, force=True, executor_threads=1)`), and analyze generic-first; `diagnose()` works against any preset's checkpoint (dynamic `<layer>_ops_slope` layer discovery), no per-preset special-casing.
- `session_diagnose_bottlenecks(run_id, analyzer_preset, view_types, metric_boundaries, traces_dir)` runs dfanalyzer with a checkpoint, then DFDiagnoser (Python API first, CLI fallback), and writes `<ws>/diagnosis.json` with severity_counts and the score >= 4 bottlenecks; pass `traces_dir` for an iteration's own `opt{N}/traces/compact/` so it does not re-analyze the shared `traces_split/`.
- Never report "no bottlenecks" from an empty diagnose(): 0 observations is a tool failure signal (see Corner cases).
- Check the absolute count/magnitude behind every severity score before treating it as a real bottleneck.
- Verify `comp=` labels by containment before recommending a fix for the category the label implies.
- Run large-trace tools inside a flux allocation (`allocation_id=<jobid>`); ask for one rather than running on the MCP server's host; tiny checks need not wait.
- Pipeline eagerly: diagnose the units that are ready, track pending ones, state coverage in the report.
- Bracket the step with `profile_step_begin(step="STEP N: dftracer-diagnoser", agent="dftracer-diagnoser")` / `profile_step_end`; retry with the SAME step string; never `profile_bind`.
- Report the smallest reliable fix path with caveats, in canonical order I/O -> communication -> memory -> compute.
- Last step: `dftracer_diagnose_check(run_id=...)` (or `diagnosis_file=` / `diagnosis_json=`) must return "pass" before the list goes to [[dftracer_optimize]].

## Constraints
- Read only the current session's trace directories (`traces/raw/`, `traces/compact/`) and metadata. Never patch source from this stage.
- Never read traces by decompressing them yourself: `view`/`aggregator`/`reader`/`event_count` or TraceViewer.
- Install env == run env: dftracer and the app share the same script, venv, modules, LD_PRELOAD/LD_LIBRARY_PATH; CC/CXX bound to the MPI the app uses; a zero exit code does not mean tracing worked (verify `import dftracer.dftracer` and a NON-EMPTY `.pfw`).
- If a tool fix needs a server restart, stop and name the tool; never reproduce its job by hand.
- Logs under `<WS>/artifacts/`; redact user paths, usernames, job ids, UUIDs, hostnames before persisting.
- Record recurring false positives in the stage skill (here), not the router.

## Corner cases
- "0 metric observations across 0 view(s)" against a checkpoint with scoreable data (VPIC 128-rank 8-node, 2026-07-14, twice; analyze had printed 340.85M POSIX ops / 325.0 MB / 196.9 MB/s and left `_flat_view_*.parquet`). Root-caused and fixed 2026-07-20 (PECAN/PDBspheres): (1) the posix/dlio presets return empty flat views when `cat` values do not match their layer names; the raw `_hlm_*_time_range_*/part.*.parquet` still had the data -> use `analyzer_preset="generic"` (`AnalyzerPresetConfigGeneric`, `auto_layers_by_category=True`), now the default; (2) `_diagnose_via_api` always returned None because it checked `hasattr(diagnoser, "diagnose_checkpoint")` on an old `dfdiagnoser` release and fell to a pandas percentile fallback that swallowed exceptions -> dfdiagnoser upgraded to a release with `Diagnoser.diagnose_checkpoint` (dynamic layer discovery, any preset), and `_diagnose_via_api` now returns real `DiagnosisFinding` objects (motif/severity/confidence/recommendation). If still 0 after both: read the analyzer's own console summary (Time Period Summary / Layer Breakdown); then suspect analyze() non-determinism ([[dftracer_analyze]]).
- Second, distinct "0 observations" cause: a stale MCP server process. A server (`dftracer-mcp-server ... --reload` and its `--_child-run` child) running for over a day, started BEFORE a `pip install --force-reinstall` of dfdiagnoser/dftracer-analyzer, keeps the old modules in memory; reported "restarts" left the same PID and start time (a new instance came up on another port while the stale one kept answering). Tell: calling `_diagnose_via_api` directly in the same venv outside the server returned real findings (15) every time. How to apply: `ps -o pid,lstart,cmd -p <pid>` on the actual server process(es), compare the start time with the last relevant pip install; if older, kill that EXACT PID and start fresh, verify something listens on the new port (curl) before retrying. Watch for several stale instances on different ports (an 11-day-old unrelated one was also found).
- `ValueError: signal only works in main thread of the main interpreter` on the FIRST diagnose_checkpoint() in a server process (2026-07-20): `Diagnoser.diagnose_checkpoint` imports `dftracer.analyzer.fact_engine` -> `dftracer.analyzer.cluster` -> `dask_jobqueue`, whose `runner.py` calls `signal.signal(SIGINT, ...)` at import time; FastMCP tools run on a worker thread. One-shot (modules are cached). Fixed by importing `dftracer.analyzer.fact_engine` eagerly at module load in `dfdiagnoser_service.py` (server main thread).
- diagnose() can score "critical" on statistically meaningless counts: VPIC `posix_close_count_sum=1.0` scored critical (percentile score on a metric with fewer than 5 operations). Check the absolute count before treating it as a bottleneck, especially POSIX/IO metrics on a workload with little file I/O.
- analyze()/diagnose() can under-count consistently (VPIC 16 ranks: 527,206 events / 4 procs vs 2,111,806 / 16); cross-check with `event_count` or the rank file listing; on disagreement aggregate `dur` by `name`/`cat` with `aggregator`/`view` (TraceViewer) and label it manual analysis. Never hand-invoke the dfanalyzer CLI (see [[dftracer_analyze]] Constraints).
- `comp=` is not proof: VPIC `dump_energies` (`comp="io"`) spent 99.7% of its time in `MPI_Allreduce`. Use nesting/containment analysis (which child spans dominate the parent) before an I/O-side recommendation.
- `async_layers` overlap diffs only against `compute_time_proc`; for a non-compute pair (data vs communication) exact event-level overlap (cross-process, from `view` events) is the reliable fallback and is exact, preferred over the bucket bounds `min(A,B)` / `max(0, A+B-bucket)`.
- A small event count can mean broken tracing, not a fast app; a single hot function can skew the summary; comparing runs with different chunk shapes misleads the comparator.
- `posix_*_ops_slope` bottlenecks (DFDiagnoser's accelerating-rate metrics: lock contention, bursty I/O, collective storms) are not covered by the proposal strategy table; see [[dftracer_optimize]] for the manual mapping.

## Optimization axes for deep-learning workloads (sweep in this order)
1. Overlap compute and I/O: `dataloader_num_workers>0`, `persistent_workers=True`, `prefetch_factor`, async checkpointing. Cheapest, usually the biggest win. (Mohan et al., Analyzing and Mitigating Data Stalls in DNN Training, VLDB 2021, https://arxiv.org/abs/2007.06775)
2. Pinned memory + CPU core affinity as ONE change: `pin_memory=True` only pays off when each rank is bound to all cores of its GPU's die; pinned to a single core, the copy thread contends with dataloader workers and the benefit inverts. On an APU (AMD MI300A) CPU and GPU share die and HBM, so affinity sets memory locality, not just scheduling. (PyTorch memory-pinning docs, https://docs.pytorch.org/docs/stable/data.html#memory-pinning)
3. File layout: minimize the NUMBER of reads and metadata calls; per-sample small files cause an open/stat/close storm on the metadata server; shard into few large files with an index. (Devarajan et al., DLIO, CCGrid 2021, https://ieeexplore.ieee.org/document/9499416)
4. System utilization: PFS bandwidth (striping; Data-on-MDT for small files) and memory bandwidth; establish bandwidth- vs compute-bound before tuning kernels. (Williams et al., Roofline, CACM 2009, https://doi.org/10.1145/1498765.1498785)
5. Compute last: mixed precision, kernel/library tuning, then algorithmic change.
Async checkpointing is a win only when checkpoint write time is a real fraction of epoch time; verify first (Mohan et al., CheckFreq, USENIX FAST 2021, https://www.usenix.org/conference/fast21/presentation/mohan; Eisenman et al., Check-N-Run, USENIX NSDI 2022, https://www.usenix.org/conference/nsdi22/presentation/eisenman).
Guard rail: a wall-clock gain from fewer checkpoints, less data read or fewer epochs is doing less, not going faster; check event and byte counts before crediting it.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
Each failed check of `dftracer_diagnose_check` -> repair, then predict (session_diagnose_bottlenecks) and validate again; the same failure twice stops the loop for the user.
- diagnosis_exists: unknown run or not diagnosed yet; check run_id (session_get_run_paths), run split/compact, then `session_diagnose_bottlenecks(run_id)`.
- diagnosis_parse: the file is truncated or not the tool's output (a crash writes `diagnosis_crash.json` instead: read its traceback); re-run the diagnosis.
- severity_counts: dfdiagnoser did not score; read `phases.dfdiagnoser.stderr`, fix the dfdiagnoser install in the server venv (and restart a stale server), re-run.
- bottlenecks_list / bottleneck_fields: re-run `session_diagnose_bottlenecks` rather than editing the file; rows come from `diagnosis/scored/*_scored.json`.
- severity_matches_score: the list was hand-edited or mixed from two runs; regenerate it (1 trivial, 2 low, 3 medium, 4 high, 5 critical).
- only_high_or_critical: the ranked list holds score >= 4 only; drop lower rows or regenerate.
- ranked: sort by score descending (regenerate).
- dfanalyzer_phase: fix the analyzer run (index force/1 thread, generic preset, allocation), then diagnose again.
- dfdiagnoser_phase: install dfdiagnoser (Python API with `Diagnoser.diagnose_checkpoint`, or the CLI) in the session/server venv; check for a stale server process; diagnose again.
- checkpoint_present: the checkpoint the diagnosis came from is gone; re-run `session_diagnose_bottlenecks`.

## Self-heal
- If a tool here is wrong, fix the tool (capability_update), not a shell workaround; if it needs a server restart, stop and say which tool.
- Before calling "0 observations" a code bug, rule out a stale server (`ps -o pid,lstart`) and the preset mismatch.

## Self-learn
- What is explored here becomes a capability: see capability_builder. Propose each new pitfall and working recipe to the main thread (target -> symptom -> root cause -> exact content) for user confirmation before it is persisted; route app/system/library specifics to workload_/system_/software_ capabilities.
- Recurring false positives are recorded here, not in the router.

## What works
- 2026-07-20 tool:diagnose: generic-preset checkpoint + dfdiagnoser with `Diagnoser.diagnose_checkpoint` gives real findings (motif/severity/confidence/recommendation) for any preset; the server imports `dftracer.analyzer.fact_engine` at startup.
- 2026-07-20 tool:diagnose (stale server): `ps -o pid,lstart,cmd -p <pid>` vs the last pip install, kill that exact PID, start fresh, curl the new port; then the same call returned 15 findings.
