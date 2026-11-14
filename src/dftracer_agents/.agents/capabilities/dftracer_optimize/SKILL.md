---
name: dftracer_optimize
description: Optimization stage orchestrator of the dftracer pipeline - route the diagnosed bottleneck list to the I/O, compute, communication and memory component optimizers, run the measured loop (session_optimization_plan / session_optimization_apply_trial, session_optimize_l1_app / l2_software / l3_filesystem, session_snapshot_l1_source, session_run_l1_iteration, session_optimization_iteration, session_generate_optimization_proposals), merge every citation-backed L1/L2/L3 finding (applied or not) into one report, and validate proposals and before/after comparisons with dftracer_optimize_check. Load to optimize a diagnosed run, or on "No optimization iterations - run session_optimization_iteration first", "diagnosis.json not found", bottlenecks reported "unsupported" (posix_*_ops_slope), a comparator showing 0 delta between iterations, an uncited/unmeasured/"do less" proposal, or a "Lustre" run that never ran on Lustre.
---
Cross-references: [[dftracer_analyze]], [[dftracer_diagnose]], [[dftracer_opt_kb]], [[dftracer_papers]], [[dftracer_optimize_io]], [[dftracer_optimize_compute]], [[dftracer_optimize_communication]], [[dftracer_optimize_memory]]

# dftracer_optimize

Pipeline stage 6. The orchestrator routes, dispatches and merges; each component optimizer
([[dftracer_optimize_io]], [[dftracer_optimize_compute]], [[dftracer_optimize_communication]],
[[dftracer_optimize_memory]]) owns its own citation search ([[dftracer_papers]]), proposal table, apply/measure
loop with the level tools of this capability, KB records ([[dftracer_opt_kb]]) and self-learning.

## Rules
- Load the plan section first (`session_read_file(run_id, subfolder=".", filepath="pipeline_plan.md")`, `## STEP N: dftracer-optimizer`) and execute it; report contradictions instead of guessing.
- Orchestration, not optimization: the orchestrator does not search papers, apply config or run iterations itself.
  1. Split the diagnosed bottleneck list by component with the tool layer's classification (`_category_sort_key` / `_metric_category`: I/O -> communication -> memory -> compute); never re-derive it.
  2. Dispatch all four component optimizers (Task) EVERY run, even with an empty slice: an exhaustive checklist may still find something, or correctly report "nothing applicable" - both are valid.
  3. Dispatch eagerly and in parallel where components do not depend on each other; synthesize incrementally.
  4. Merge the four reports into ONE comprehensive report.
- Every dispatch carries: run_id and the resolved system/allocation/filesystem facts; the component's bottleneck slice (may be empty); the metric objective and max-iteration budget; a reminder that the report must include the FULL dimension-checklist verdict table (applied / applicable-not-measured / not-applicable with reason); the profiling step heading per component (e.g. "STEP 7a: dftracer-optimizer-io", "7b ... compute", "7c ... communication", "7d ... memory"); which files/proposals are its own vs a sibling's.
- Parallel dispatches on SEPARATE allocations against the SAME `annotated/source/` tree: the tree is shared and unversioned; a concurrent edit to a file another rank set is loading can desync a multi-rank DDP run (real incident: NCCL "remote process exited", twice in one session). Tell each subagent its files, prefer changes behind an env-var gate defaulting to pre-change behavior (and pin a sibling's known gate to its safe value in your own run script), or give each its own source snapshot; on an unexpected crash, check for a diff in files you did not touch before blaming your own change.
- The optimization loop (each component, MANDATORY order):
  1. RECALL then SEARCH: `opt_kb_lookup(system, workload, software)` then `session_search_optimization_context(run_id, system, workload)` (every detected layer, local-first via opt_kb + `rag_search`, then remote; also benchmark targets in `context_opportunities.json`); read the `notes` (where "accepted but ignored" caveats live). Then an independent literature pass every iteration (`search_arxiv` / `search_semantic_scholar` / `session_search_optimization_papers` / `rag_search`) for techniques matching the observed behavior even if the diagnosis did not rank them; both streams feed one table, and every literature-sourced technique tried is `opt_kb_record`ed with its citation and a "literature-pass finding" note.
  2. PROPOSE as a citation-backed table: `session_generate_optimization_proposals` then `opt_proposal_table`. Every candidate of the component's checklist is a row, applied or not, with a one-line reason for each not-applied row; every row carries `app_impact_pct` and `system_impact_pct` (POTENTIAL, bounded by the measured bottleneck bucket, 0 when not applicable, never missing, measurement basis stated) and, once measured, `actual_app_impact_pct` / `actual_system_impact_pct` side by side; the tool's 50/50 weighted-score sort IS the apply order.
  3. APPLY ONE change, measure at the SAME scale as the baseline, `opt_kb_record` (scope honestly: machine -> system, library -> software, application -> workload), no-ops and regressions too. Never bundle changes.
  4. PUBLISH: `opt_kb_render()` once, after all four components' records landed.
- Ordering: canonical I/O -> communication -> memory -> compute across components, L1 -> L2 -> L3 within one. Software hints and system tuning are near-no-ops while the application serializes the bottleneck (Flash-X: ROMIO hints bought 18% while one rank wrote 91% of bytes; 7.6x only after the L1 rebuild removed the single-writer funnel).
- Replicates: at least 5 per iteration (baseline, each candidate, final best) before `comparator` or `opt_kb_record`; CV = stddev/mean on the primary metric, if above ~10-15% go to 8, then 10 replicates. Compare medians (p50) and state p95/min/max for both sides; REGRESSED/CONVERGED/EXHAUSTED verdicts come from the percentile comparison; `opt_kb_record` before/after are medians, notes carry the p95/min/max band and replicate count.
- metric_scope axis (orthogonal to L1/L2/L3): "app" (the app's own trace metric, default) or "system" (aggregate achieved bandwidth/FLOPs/message rate, a trace-derived proxy). A system-scope record must carry the paired app metric; if the app regressed > 2% the KB forces "regression" (`guard_triggered: true`): treat it as REGRESSED and revert.
- Merge and report (MANDATORY): (1) one merged checklist table, all four components, grouped by component then L1/L2/L3, every verdict and every non-applied REASON verbatim, never a dropped row; columns `#, Strategy, Description of Optimization, Potential App Impact, Potential System Impact, Actual App Impact, Actual System Impact, Weighted Score, Citation, Type, Prior result here, Status, Why (if not applied)`, built with `opt_proposal_table` (never re-sort by hand; uncited rows are rejected); (2) one merged iteration/results table with citations, deltas and replicate noise bands in canonical order; (3) the best overall configuration, calling out conflicts between components' winners instead of silently picking one; (4) cross-component shared dimensions (NUMA binding, cache blocking, compute/communication overlap) merged into one row naming both components; (5) `opt_kb_render()`; (6) splice the merged results into `pipeline_plan.md` with `session_edit_file` (anchor e.g. on "## STEP 9: dftracer-report"), never a full `session_write_file` rewrite; append the changelog the same way; if `session_edit_file` is unavailable, write the block under `artifacts/` and report the splice point.
- Re-diagnose after the loop: trace and diagnose the post-optimization state ([[dftracer_diagnose]]), compare with the starting bottleneck list (ranking shifts, newly visible bottlenecks), estimate the next round's potential ceiling, report, and ASK the user whether to iterate again. Never loop automatically.
- Capture the run record at the end of each successful run: `session_capture_run_record(run_id, run_name, prev_run_name, source_path, run_script, run_log, param_files, notes)` and `session_snapshot_run_source` with a source_path outside `<run_name>/`.
- Profiling: bracket the orchestration (routing, dispatch, merge) with `profile_step_begin(step="STEP 7: dftracer-optimizer", agent="dftracer-optimizer")` / `profile_step_end`; never `profile_bind`; a subagent reporting "nothing applicable" is a successful dispatch.
- Return: the merged checklist table, the merged results table, the best configuration (or the honest report that no component found a win), what was NOT verifiable at this scale, and each component's self-learning proposals relayed distinctly attributed, unfiltered.
- Last step: `dftracer_optimize_check(proposals_json=<the merged table or opt_proposal_table output>, comparison_json=<baseline vs best, both runs>)` must "pass"; use `stage="generated"` on raw `session_generate_optimization_proposals` output.

## Constraints
- Never propose "do less": fewer/less frequent checkpoints, fewer plot variables or less data written, less I/O/compute/communication/memory, skipped synchronization/verification the app's correctness depends on, fewer epochs, smaller inputs, or reduced precision/correctness. Make the SAME work faster.
- Never change the workload's actual behavior (I/O access pattern, communication topology/collective semantics, algorithm, memory footprint/lifetime) because another shape measured faster; that includes request/transfer/batch/tile-size sweeps (standing rule 2026-07-10). "Shape A is slower than B" is a diagnostic, not a license.
- Allocation-aware runs: ask for the user's active allocation (use an existing one via `flux proxy <JOBID> ...` or spawn one, user's choice; check `flux jobs -no "{id} {state} {t_remaining}" <JOBID>`), use the plan's node count with `--exclusive`, a problem size large enough to resolve the metric, data on the system-detected PFS (never /tmp or home), same run class for baseline and iterations (never smoke vs production); the tracer stage runs the jobs. Never block on a long `flux proxy` in the foreground.
- Run length: at least ~10 minutes of training (DL) so checkpoint/collective effects exceed noise; ask the user for a time budget, calibrate seconds/epoch on the baseline, fix `epochs = floor(budget / s_per_epoch)` and problem scale, dataset size and checkpoint_interval for every variant; watch early-exit knobs (e.g. `target_dice`).
- Tool-first; if a tool fix needs a server restart, stop and name the tool. Logs under `<WS>/artifacts/`. Redact before persisting (Citation lines exempt).

## Corner cases
- `session_generate_optimization_proposals` reports `posix_*_ops_slope` bottlenecks as "unsupported" (the strategy table covers absolute bandwidth/IOPS metrics, not slopes; DFDiagnoser emits them for accelerating rates: lock contention, bursty I/O, collective storms). Derive proposals: slope > 1 means accelerating; `posix_data_ops_slope` -> larger transfer size (L1), ROMIO hints (L2), stripe tuning (L3); `posix_close_ops_slope` -> stagger close timing (L1), `ind_wr_buffer_size` (L2), client cache (L3); `posix_metadata_ops_slope` -> shared file instead of file-per-process (L1), pre-create (L2), DNE (L3); cite the Lustre papers the iteration search found.
- `session_optimization_iteration` copies ALL previous iteration traces into `opt{N}/traces/` before the new run and splits the combined set, so `comparator` shows 0 delta (identical split chunks; `trace_files=[]`). Isolate the new traces (files in opt{N}/traces/ not in opt{N-1}/traces/) into a clean directory, `split(directory=<clean>, output_dir=<clean_split>, force=True)`, then `comparator(baseline=opt{N-1}/traces_split, variant=<clean_split>)`.
- HDF5 1.10.x silently no-ops several L2 optimizations: `H5Pset_page_buffer_size` on an MPI-IO fapl and `H5Fcreate_async` (falls back to sync). Use HDF5 >= 1.14 for parallel I/O (`--enable-parallel`, rebuild the app with HDF5_DIR pointing at it; verify `h5cc -showconfig | grep Version:`).
- Measurement validity: a "moved to Lustre" result is invalid until the execution directory is proven to be on Lustre (Pegasus/PMC executes in `<CWD at plan time>/wf-scratch/...`, not the site catalog's sharedScratch); leftover intermediate files from a prior run skew unique_file_count/total_bytes. Confirm op count and bytes are unchanged before/after so a gain is not doing less.
- An L1 change that rewrites a mature scientific library's numerical I/O path without correctness regression tests is rejected even if it would hit a checklist item (Montage per-row FITS I/O bounds memory by design).
- The levels/iteration tools need prior state: `session_optimization_plan` and the level tools return "No optimization iterations - run session_optimization_iteration first" on a fresh run; `session_generate_optimization_proposals` takes `bottlenecks_json` for runs launched externally (e.g. a `flux batch` job) and otherwise reads the diagnosis.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
Each failed check of `dftracer_optimize_check` -> repair the input or the predictor, then predict and validate again; the same failure twice stops the loop for the user.
- proposals_parse: pass the proposals list, or the JSON of opt_proposal_table / session_generate_optimization_proposals.
- proposals_present: generate proposals from the diagnosed bottlenecks (`session_generate_optimization_proposals`, `bottlenecks_json` for external runs) first.
- level_is_L1_L2_L3: set level L1 (application), L2 (software/middleware) or L3 (system) on every row.
- citation_backed: cite a paper (arXiv/DOI) > docs > web; `session:<run_id>` only for a result measured in this pipeline; search with [[dftracer_papers]] and check with dftracer_papers_check.
- never_do_less: drop the row or replace it with a lever that keeps the work (buffering, overlap, collective tuning, caching, faster compression, layout/binding).
- impact_fields: fill both potential impact fields (0 when not applicable, never missing) with the measurement basis.
- status_valid: status is proposed, applied, not-applied or deferred.
- skip_reason_for_not_applied: keep every considered candidate with its skip_reason (not the diagnosed bottleneck, no budget, superseded, KB shows a no-op).
- applied_rows_measured: measure the change (>= 5 replicates, comparator on medians), fill actual_app_impact_pct, then opt_kb_record it.
- comparison_parse: pass `{"baseline": {"run_id", "metric", "value"}, "variant": {...}}`.
- comparison_has_both_runs: give both the baseline and the optimized run.
- comparison_runs_distinct: compare against the baseline, not the same run; isolate the iteration's own traces (see Corner cases).
- comparison_values: give both measured values (medians).
- comparison_same_metric: compare the same metric on both runs.

## Self-heal
- A wrong or missing level/iteration tool is fixed in the tool (capability_update), not worked around by hand; if it needs a restart, stop and say which.
- An unexpected crash during a parallel dispatch window: check for diffs in files you did not touch before blaming your own change.

## Self-learn
- What is explored here becomes a capability: see capability_builder. Each component's proposals are relayed to the main thread for user confirmation before persisting; measured results go to the KB (opt_kb_record) so the next session starts from them.
- A generic rule learnt here reaches every agent (shared sections), not one corner of one skill.

## What works
- 2026-06-24 ior/hdf5/vast (Cray MPICH, 192 procs, 2 nodes, 48 GiB): `MPICH_MPIIO_HINTS="*:romio_cb_write=enable"` with IOR `-a HDF5 -b 64m -t 16m -s 4 -c -Y`: total 168.8 -> 112.9 s (-33%), write 352 -> 557 MiB/s, read 1705 -> 1991 MiB/s, POSIX calls 667,363 -> 73,991, seek slope 362 -> 9.96, data slope 74.3 -> 2.19. Never romio_ds_write=disable or romio_cb_read=enable on VAST.
- 2026-07-06 montage/pegasus L2: `posix_fadvise(fd, 0, 0, POSIX_FADV_SEQUENTIAL)` on a separate fd after each `fits_open_file()` on the hot read path (`#define _DEFAULT_SOURCE` first, then `#include <fcntl.h>`): bandwidth 740.6 -> 859.8 MB/s, POSIX time 1.096 -> 0.944 s, ops/bytes unchanged (the L3 Lustre part of that result was later shown invalid, see LESSONS).
- 2026-06-22 tool:comparator between iterations: isolate the new traces, re-split with force=True into a fresh output_dir, compare against the previous split.
