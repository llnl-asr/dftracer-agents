---
name: dftracer_optimize_io
description: I/O component of the dftracer optimizer - the exhaustive 12-dimension I/O checklist, WisIO/Drishti/GLANCED-IO bottleneck-to-optimization mappings, and L1 (app) / L2 (middleware) / L3 (filesystem/OS) strategy catalogs, with a validator for the citation-backed findings table. Load for small_io, small_read/small_write, rand_pct / low seq_pct, read_time, write_time, metadata_time, fetch_pressure, epoch_straggler, checkpoint/intensity or fs_bw bottlenecks; a dataset on NFS; Lustre striping, Data-on-MDT, Rabbit/near-node flash; ROMIO/MPI-IO, HDF5, NetCDF or PyTorch DataLoader tuning; low compute/I-O overlap; or an A/B whose delta may be tracing overhead or cache drift.
---
Cross-references: [[dftracer_optimize]], [[dftracer_opt_kb]], [[dftracer_papers]], [[dftracer_analyze]], [[dftracer_diagnose]], [[dftracer_optimize_compute]], [[dftracer_optimize_communication]], [[dftracer_optimize_memory]], [[software-mpi]], [[software-hdf5]], [[software-posix]], [[software-ray]], [[system-tuolumne-rabbit]], [[workload-vpic-kokkos]], [[workload-ior]], [[workload-h5bench]], [[workload-1000genome]]

# dftracer_optimize_io

The I/O component of the optimizer pipeline (converted from the dftracer-agents `dftracer-io-optimization` skill and the `dftracer-optimizer-io` agent). The orchestrator ([[dftracer_optimize]]) dispatches this alongside the compute/communication/memory siblings with the I/O slice of the diagnosed bottleneck list.

## Rules

- **First step: `opt_io_checklist(bottlenecks_json=<diagnosis>)`.** It renders the 12 dimensions, the built-in L1/L2/L3 strategies per I/O metric (from `strategies.py`), the default citation (WisIO) and the bottlenecks that belong to I/O. **Last step: `dftracer_optimize_io_check(findings_json=<one row per dimension>)`** must return `pass` before the findings go back to the orchestrator.
- **Exhaustive Dimension Checklist (walk ALL 12, every session - user instruction 2026-07-10).** Do not stop at the top-ranked bottleneck's obvious fix. Every optimization loop walks the full checklist and records a verdict for EACH category, not just the ones that turned out to matter; this is the enumeration source for the "Show the WHOLE option space" proposal table. Verdict per category: **Applied & measured** (result + citation), **Applicable, not measured** (concrete reason), or **Not applicable** (concrete technical reason: wrong FS_TYPE, no literature found for this access pattern, structurally inert given the app's I/O shape, admin-only/already-maxed per the system skill, ...). Never silently omit a category, and never mark "not applicable" without first running the literature search for it ("I didn't look" is never a reason).
  1. L1 buffering (write coalescing/batching/double-buffering)
  2. L1 caching (chunk cache, app-level LRU, page-cache hints)
  3. L1 prefetching (async I/O, HDF5 async VOL, `posix_fadvise`, background threads)
  4. L1 stage-in/stage-out (faster intermediate storage, pattern unchanged)
  5. L2 MPI-IO/ROMIO (collective/two-phase buffering, data sieving, `cb_nodes`/multiplier)
  6. L2 HDF5 (chunk cache sizing, metadata block size, collective metadata ops, page buffering)
  7. L2 other I/O middleware (NetCDF/PnetCDF, compression filters, framework dataloader overlap)
  8. L3 filesystem striping/layout (Lustre `lfs setstripe`/PFL/Data-on-MDT, GPFS/BeeGFS block size)
  9. L3 near-node/burst-buffer acceleration (Rabbit/DW, node-local NVMe, `/dev/shm`, VAST cache)
  10. L3 OS/kernel tuning (readahead, VM/dirty-ratio, I/O scheduler, NUMA binding - check tunability first)
  11. Metadata-path tuning (Lustre metadata striping, Data-on-MDT, directory sharding)
  12. Compute/communication overlap (async checkpointing, overlapping compute with I/O)
  See the L1/L2/L3 Strategy sections below for the technique catalog behind each number and the FS_TYPE-gated tables for which system/filesystem combinations each technique applies to.
- Every row in the resulting `opt_proposal_table` MUST carry `app_impact_pct` (% of current application wall time, measured or bounded estimate) and `system_impact_pct` (throughput/bandwidth/utilization effect, 0 if not measured/applicable) - columns `#`, `Strategy`, `Description of Optimization`, `App Impact`, `System Impact`, `Weighted Score` (50/50 weighted, auto-sorted descending by the tool). Never omit these fields.
- **Tool-first.** Every analysis/diagnosis step tries the MCP tools before manual methods, in this order: `analyze`, `diagnose`, `comparator`, `event_count`, `reader`, `session_analyze_traces` ([[dftracer_analyze]], [[dftracer_diagnose]]). For optimizing: `opt_kb_lookup` -> `session_search_optimization_context` (step "0.5", every iteration, before proposal generation) -> `session_generate_optimization_proposals` -> `session_optimize_l1_app` / `session_optimize_l2_software` / `session_optimize_l3_filesystem` (pass the I/O metric the diagnosis surfaced) -> `session_optimization_iteration` -> `comparator` -> `session_search_optimization_papers` / `rag_search` -> `search_arxiv` / `search_semantic_scholar`. If tools are unavailable, ask the user to start the dftracer MCP server; if they error, fix the tool or wiring before custom Bash/Python.
- **Explicit separation.** Every report has a table separating **TOOL FINDINGS** (dfanalyzer, dfdiagnoser, comparator, proposals, paper search) from **MANUAL ANALYSIS** (custom Bash/Python, only when tools fail). Label each finding with its source; never conflate.
- **Independent literature/KB search pass (standing default, not diagnosis-gated).** Every iteration: (1) KB recall `opt_kb_lookup(system, workload, software=<detected stack>)`; (2) unconditionally search (`search_arxiv`, `search_semantic_scholar`, `session_search_optimization_papers`, `rag_search`) for techniques that match the OBSERVED I/O access pattern even when the diagnosis does not rank a matching bottleneck. Both streams feed the same proposal table. Every literature-sourced technique tried, pass or fail, is `opt_kb_record`ed with its citation and a note "literature-pass finding, not diagnosis-ranked".
- **Citation-backed (MANDATORY).** Every proposal carries a verifiable citation: authors, title, venue/year, URL (arXiv, DOI, IEEE Xplore) and the 1-2 sentence finding that supports it. Use the Built-in Citations (WisIO, Drishti, GLANCED-IO) or search (see Citation Rule below). No paper after 10 search attempts -> mark the proposal UNSUPPORTED, do not apply it, and write "no L<N> fix - no citation available".
- **The optimization loop.** (1) recall + exhaustive search (`opt_kb_lookup`, `session_search_optimization_context`; read `context_opportunities.json` snippets yourself for `benchmark_targets` numbers, the tool deliberately does not parse them); (2) propose as a cited TABLE with `opt_proposal_table` listing EVERY checklist candidate, applied or not, with a one-line reason for each not-applied row; (3) apply ONE change, re-run at the SAME scale as the baseline, measure, `opt_kb_record` (no-ops and regressions too; choose `scope` honestly: machine -> system, library -> software, app -> workload); (4) `opt_kb_render`.
- **Ordering.** Address bottlenecks in canonical I/O -> communication -> memory -> compute order across components (severity only breaks ties within a component) and **L1 -> L2 -> L3** within a component: software hints and system tuning are near-no-ops while the app itself serializes the bottleneck (Flash-X: ROMIO hints bought 18% while one rank still wrote 91% of the bytes; 7.6x only arrived after the L1 rebuild removed the single-writer funnel).
- **Two axes: layer x metric_scope.** layer (`scope` in opt_kb: L1 workload / L2 software / L3 system) answers *who inherits this finding*; `metric_scope` (`app` default / `system`) answers *which metric moved*. On this pipeline a "system metric" is a **trace-derived proxy** (aggregate bytes/s from the app's own trace), not OST/MDT telemetry - don't present it as more precise than it is.
- **Non-degradation guard (MANDATORY).** `opt_kb_record(metric_scope="system", ...)` requires the paired `app_metric`/`app_before`/`app_after` for the SAME change; if the app metric regressed > 2% the KB forces `regression` (`guard_triggered: true`). Treat `guard_triggered: true` exactly like a regression: revert the change.
- **Validate every applied optimization** by re-running and comparing: identical op count / data volume with better bandwidth/time = a real, safe win. On LLNL systems verify you are ACTUALLY on the filesystem you think you are on.
- **Replicates.** Every iteration (baseline, each candidate, final best) is measured with >= 5 replicates before `comparator`/`opt_kb_record`; CV > ~10-15% -> 8, then 10 replicates. Compare p50 (primary) and state p95/min/max for both sides; `opt_kb_record` before/after are medians and `notes` carries the p95/min/max band and replicate count. A delta is claimable only when its full range does not overlap the baseline's.
- **Pipeline eagerly (user instruction 2026-07-10).** When a multi-workload/multi-replicate baseline is partially complete, start analysis/diagnosis/optimization on the units that ARE ready; track included vs pending units, feed pending ones in as they complete, and state partial coverage in every report.
- **Iteration directory structure.** All artifacts live under the iteration's own `opt<n>/` directory (`opt<n>/source/`, `opt<n>/patches/`, `opt<n>/scripts/`, `opt<n>/traces/{raw,compact}/`), never a hand-built path like `ws/opt_results/`. Before a new iteration and again after applying L1/L2/L3 changes, call `session_validate_structure(run_id)`; if `clean=false`, `session_reorganize_structure(run_id, dry_run=False)` before the smoke test or trace collection.
- **Step profiling.** Bracket the whole execution with `profile_step_begin(step="STEP N: dftracer-optimizer-io", agent="dftracer-optimizer-io")` ... `profile_step_end(..., status="ok")` using the plan heading verbatim; never call `profile_bind`; never report ok for a step that failed.
- **Return**: the iteration table (applied opts, deltas, citations), the FULL checklist verdict table, the best config, and an honest note on what was not verifiable at this scale. Propose new pitfalls and the working recipe to the main thread for user confirmation; never write lesson/skill files directly.

## Constraints

- **NEVER "do less" as optimization.** Forbidden: "reduce checkpoint frequency"/"write fewer checkpoints", "reduce plot variables"/"write less data", "do less I/O/compute/communication", "use less memory", skipping synchronization/verification the app's correctness depends on, any proposal whose core mechanism reduces work, precision or correctness. Make the SAME work faster (buffering, async I/O, collective I/O, faster compression, stripe tuning). A wall-clock gain from fewer checkpoints, less data or fewer epochs must be checked against event and byte counts before it is credited.
- **NEVER change the app's actual I/O access pattern (standing rule 2026-07-10).** Do not flip `MEM_PATTERN`/`FILE_PATTERN` INTERLEAVED->CONTIG, random->sequential, etc. because the other pattern measured faster: that changes the workload and can break correctness. "write (INTERLEAVED) is slower than read (CONTIG)" is a valid DIAGNOSIS, not a license. This **explicitly includes transfer-size/request-size sweeps (re-affirmed 2026-07-10)**: "4 MB transfers beat 4 KB" bounds the headroom, but the best config keeps the real request size and serves it faster (ROMIO two-phase/collective buffering, HDF5 chunk/page caching, readahead, burst-buffer/node-local stage-in). Keep the pattern and use buffering, caching, prefetching, stage-in/out, collective/aggregator tuning, metadata/layout tuning or filesystem layout instead; search the literature for those techniques for the OBSERVED pattern. Enforced at record time by [[dftracer_opt_kb]] Rule 5.
- **Datasets must live on Lustre, never NFS.** Application datasets (training data, fractals/checkpoints/runs, any file read/written repeatedly) go on Lustre (`${LUSTRE}/<user>/...`), never NFS-backed workspace, home or collab paths. NFS is single-server with no striping and hard-caps aggregate bandwidth/IOPS however well L1 is tuned. Before any benchmark/training job: `stat -f <dataset_dir>` (Type: nfs vs lustre) or `df -T <dataset_dir>`; if NFS, copy to Lustre and repoint the app's data-dir argument - do not analyze an NFS-resident dataset.
- **Every optimization iteration writes data to the system-detected PFS**, never `/tmp` or home (dftracer-preload-run PFS rule; use the PFS path from system detection).
- **L3 FS_TYPE gate (MANDATORY).** Confirm the detected FS_TYPE (Step 8-PRE of dftracer-pipeline) before proposing any L3 strategy; only propose strategies whose "Valid FS_TYPE" matches. Wrong-filesystem strategies are silently harmful (e.g. `romio_ds_write=disable` is a 4x write regression on VAST NVMe, safe on Lustre spinning disk). Every L3 proposal includes a rollback command and a privilege class (`no-sudo | sudo | admin-only`); NFS/GPFS/BeeGFS tuning is a MANUAL RECOMMENDATION only and never applied to lustre, vast or local filesystems.
- L2 changes are configuration only (env vars, hint files, runtime config), reversible, with the original value documented; collect L2 env vars in `opt<n>/.../opt_l2_env.sh` and source it before every run.
- L1 changes to a mature scientific library are high-risk: only with a correctness check (byte-identical output before/after); prefer the lower-risk L2/L3 hints from the layer skills (software-posix, software-mpi, software-hdf5).
- **Never bundle changes** in one measurement: one lever per iteration, or the KB entry's attribution is a lie.
- **Tracing-state pre-check (MANDATORY, 2026-07-24).** Before comparing a variant against a baseline, grep both launch scripts for `DFTRACER_ENABLE`/`DFTRACER_INIT`; refuse the comparison and re-measure if they differ. Report headline bandwidth from untraced runs; trace collection is a separate instrumented pass. Every replicate writes to a fresh, unused output path and arms are interleaved (v0, v1, v0, v1 ...), never run back-to-back by arm.
- **Allocation rules (production runs).** Ask for the active allocation ID if unknown; check it is `R` in `flux jobs`; use the plan's node count with `--exclusive` (`flux proxy <alloc> flux run -N <n> -n <tasks> --exclusive ...`); problem size large enough that noise does not dominate; never compare a smoke test against production; the tracer agent runs the jobs, this agent proposes, applies config and reads results.
- **Run length.** Target at least ~10 minutes of training/run time; for DL ask the user for the time budget, calibrate seconds/epoch on the baseline, fix `epochs = floor(budget/s_per_epoch)` for every variant, and also fix `problem_scale`, dataset size and `checkpoint_interval` unless that knob is the one under test (say so); watch for early-exit knobs (`target_dice`).
- **Environment consistency.** The app defines the environment: reuse its install/job scripts verbatim; install env == run env; dftracer in the same venv/script as the app; `CC`/`CXX` bound to the app's MPI; a zero exit code does not mean tracing worked (verify `import dftracer.dftracer` and a non-empty `.pfw`).
- **Shared source tree in parallel dispatch.** When a sibling component optimizer runs in parallel on the same `annotated/source/` tree: know which files are yours, pin the sibling's known env-gates (e.g. `PECAN_TORCH_COMPILE`) to off in your run script, wait/coordinate if its edit is in flight, and on an unexpected crash check for diffs in files you did not touch before blaming your change.
- Context economy: locate with `graph_query`/graphify (`graphify query|explain|affected|update`, CLI not graphify-mcp) instead of reading whole trees; run `affected` before editing a shared function. Logs go to artifacts; capture the run record before finishing; redact before persisting.

## Corner cases

- **Op-count-bound vs bandwidth-bound (vpic-kokkos, 2026-07-14).** 340.85M POSIX ops moved only 325.0 MB (sub-byte average transfer) and cost 1.65 s of a 210.2 s job: real but structurally negligible, op-count-bound. ROMIO/striping target bandwidth-bound patterns and do nothing here; only app-level write coalescing helps. Always check that I/O time is a meaningful fraction of job time AND whether it is few-huge-ops or many-tiny-ops before a striping/ROMIO fix (see workload-vpic-kokkos).
- **Split POSIX metadata ops by resolved path** before proposing a metadata-layer fix. In Python/framework stacks (Ray, PyTorch) metadata counts are dominated by interpreter/venv import traffic (`stat`/`open` on every site-packages module), invisible to striping, Data-on-MDT or ROMIO - a dynamic-loading problem (Frings et al., *Scalable Massively Parallel I/O to Task-Local Files*, ICS 2013, the Spindle problem). 2-node Ray Train: 117,920 `__lxstat64` calls were venv import traffic; the dataset file was 9 ops / 0.006 s. Resolve trace `FH` records to paths, report import traffic and application data separately.
- **Bulk archive after a small-file producer (1000genome `individuals.py`, 2026-07-26).** Before striping/ROMIO, look for `tarfile.add(dir)` / write-then-reopen right after a many-small-file loop: it re-touches every file (open+stat+read+close per member). Stream members into the open TarFile (`tarfile.addfile(TarInfo, BytesIO)`). Measured `IND_TAR_STREAM=1` -13.2% standalone; its relative share grew once the companion compute fix collapsed parse time (see workload-1000genome).
- **Striping must match the observed file and transfer size** (table below); set it with `lfs setstripe` BEFORE any file is created (it cannot change retroactively without rewriting). Many small per-sample files: `lfs setstripe -c 1 <dataset_dir>`, never a high count "for safety". Verify with `lfs getstripe -c <dir>` and `lfs getstripe <sample_file>` (one OST).
  | Access pattern (from trace) | Stripe count | Stripe size |
  |---|---|---|
  | Many small files, each < stripe size (ML per-sample, KB-few MB) | `1` (parallelism comes from many files) | filesystem default |
  | Few large shared files read/written by many ranks (checkpoints, HDF5, one big dataset) | OST count, capped 8 for < 1 GB, 16+ for very large | 1-4 MB aligned to the transfer size (`dftracer_info` / diagnose `*_avg_transfer_size`) |
  | Sequential large writes (checkpoints) | 4-8 | 4 MB (ROMIO `cb_buffer_size` 64 MB / 16 aggregators) |
- **Low compute/I-O overlap with "correct" DataLoader knobs (GLANCED-IO OVERLAP-1).** Verify dataloader and compute events actually overlap in the timeline; a sequential load->compute->load signals a stall whose cause is often a lower layer (small-I/O pattern, wrong filesystem, unmatched striping), not num_workers/prefetch_factor.
- **Async checkpointing** is a win only when checkpoint write time is a real fraction of epoch time - verify first (CheckFreq, Check-N-Run).
- **Cache/layout drift between campaigns.** Filesystem/cache state drifted ~11% between two back-to-back same-night Lustre campaigns, larger than most effects under test; back-to-back-by-arm ordering biases later arms.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector

`dftracer_optimize_io_check` names the failed check; repair, re-run `opt_io_checklist` if the dimension set is in doubt, then check again. The same failure twice stops the loop and goes to the user.
- `findings_parse` -> pass a JSON list of rows, `{"rows": [...]}`, or a path to such a file, with the `row_fields` that `opt_io_checklist` returns.
- `findings_present` -> the table is empty: record one row per checklist dimension.
- `every_dimension_has_a_verdict` -> add a row for each missing dimension number (1-12): applied, applicable (with reason) or not_applicable (with reason, after the literature search for it).
- `verdicts_valid` -> use applied | applicable | not_applicable (or "Applied & measured", "Applicable, not measured", "Not applicable").
- `level_is_L1_L2_L3` -> every applied/applicable row names its layer L1/L2/L3 (or workload/software/system).
- `level_matches_dimension` -> dimensions 1-4 are L1, 5-7 L2, 8-10 L3 (11 and 12 any): move the row to the right dimension or fix its level.
- `citation_backed` -> cite a paper (arXiv/DOI) > docs > web, a built-in key (wisio, drishti, ...) or `session:<run_id>` for an in-house measurement; search via [[dftracer_papers]]; still nothing after 10 attempts -> mark UNSUPPORTED/not applied.
- `reason_for_not_applied` -> give the concrete technical reason (wrong FS_TYPE, admin-only, structurally inert, no literature after searching, budget).
- `impact_fields` -> fill numeric `app_impact_pct` and `system_impact_pct` on applied rows (0 when not applicable, never missing).
- `never_do_less` -> the row reduces work (fewer epochs/steps, smaller input, skipped output/checkpoints): drop it and use a lever that keeps the work.
- `component_rules` -> a dataset placed on NFS (stage it to Lustre or a near-node tier) or an access-pattern change (keep the pattern; buffer, cache, prefetch, stage instead).

## Self-heal

- If `opt_io_checklist` or `dftracer_optimize_io_check` is wrong (a dimension, a level, a false rule hit), fix the tool with capability_update; do not work around it in shell.
- If an MCP optimization tool errors, fix the tool or its wiring before falling back to manual Bash/Python, and label any manual result MANUAL ANALYSIS.

## Self-learn

- What is explored here becomes a capability: see capability_builder. A new measured I/O result is `opt_kb_record`ed (no-ops and regressions too) and a new pitfall becomes a lesson here; a new workload/software/system fact goes to its workload_/software_/system_ capability. Propose lessons and recipes to the user before persisting them.

## What works

- 2026-07-14 (vpic-kokkos, 128 ranks, Tuolumne): checking op count vs bytes moved first (340.85M ops / 325 MB / 1.65 s of 210.2 s) avoided a useless striping/ROMIO change; the I/O was negligible.
- ScaFFold on Tuolumne: moving the fractal dataset from NFS to Lustre with matched striping removed the `posix_*_ops_slope` ceiling (~41% compute/I-O overlap on NFS) with no app code change.
- Many-small-file ML datasets: `lfs setstripe -c 1 <dataset_dir>` before copying data in, verified with `lfs getstripe`.
- 2026-07-24 (IOR 4.0.0, Tuolumne, HDF5 1.14.5): untraced headline runs, matched tracing state across arms, a fresh output directory per replicate and interleaved arms gave a trustworthy A/B (a traced baseline had made untraced variants look ~60% faster).
- 2026-07-26 (1000genome `individuals.py`): streaming tar members (`IND_TAR_STREAM=1`) -13.2% standalone.

## Reference: related skills

Software-specific strategies are also available in dedicated skills:
- **[[software-mpi]]** — MPI-IO/ROMIO details, Flux env propagation, Cray MPICH
- **[[software-hdf5]]** — HDF5 version compatibility, chunk/cache tuning, build from source
- **[[software-posix]]** — POSIX readahead, Lustre striping, OS/VM tuning, ops_slope bottlenecks
- **[[software-ray]]** — Ray/Ray Data caveats, including `ray.data.read_csv` object-store materialization (no per-epoch re-read)
- **[[workload-vpic-kokkos]]** — worked example of an op-count-bound, structurally negligible I/O profile

Workload-specific results:
- **[[workload-ior]]** — quantified ROMIO results on VAST NVMe (Tuolumne)
- **[[workload-h5bench]]** — HDF5 CMake build and annotation pitfalls


System-specific accelerators (L3 near-node storage tiers):
- **[[system-tuolumne-rabbit]]** — Rabbit near-node flash on Tuolumne (XFS/GFS2/Lustre via Flux `-S "#DW ..."`); stage hot data onto a local flash tier to relieve network-Lustre bottlenecks

---

## Reference: key papers

### WisIO (primary bottleneck → optimization guide)

**Citation:** Yildirim, Izzet, Hariharan Devarajan, Anthony Kougkas, Xian-He Sun, and
Kathryn Mohror. "WisIO: Automated I/O Bottleneck Detection with Multi-Perspective Views
for HPC Workflows." In *Proceedings of the 39th ACM International Conference on
Supercomputing*, pp. 749–763. 2025.

**What it provides:**
- Multi-perspective bottleneck taxonomy covering: sequentiality, small-I/O,
  read/write time fraction, metadata overhead, fetch pressure, stragglers
- Per-category root cause + optimization strategy mapping
- Quantified thresholds for high/critical classification

**Bottleneck → optimization mapping from WisIO:**

| WisIO category      | Metric in dfdiagnoser | Primary fix (L1→L3)                              |
|---------------------|-----------------------|--------------------------------------------------|
| small-io            | small_io_pct          | L1: buffer reads; L2: collective I/O; L3: stripe |
| sequentiality       | rand_pct / seq_pct    | L1: sort access; L2: prefetch hint; L3: readahead|
| read-time           | read_time_pct         | L1: async I/O; L2: cb_buffer; L3: blockdev setra |
| write-time          | write_time_pct        | L1: async write; L2: dirty tuning; L3: vm.dirty  |
| metadata            | metadata_time_pct     | L1: cache stats; L2: HDF5 metadata opt; L3: MDT  |
| fetch-pressure      | fetch_pressure        | L1: DataLoader workers; L2: prefetch_factor       |
| stragglers          | epoch_straggler       | L1: sort by size; L2: persistent_workers          |


### Drishti (user-facing optimization guidance)

**Citation:** Bez, Jean Luca, Hammad Ather, and Suren Byna. "Drishti: Guiding
end-users in the I/O optimization journey." In *2022 IEEE/ACM International Parallel
Data Systems Workshop (PDSW)*, pp. 1–6. IEEE, 2022.

**What it provides:**
- End-user-facing optimization suggestions organized by I/O category
- Three-level suggestion structure (application / library / system) matching our L1/L2/L3
- Estimated improvement ranges per suggestion type

**Drishti suggestion model:**

| Drishti category | L1 (app)                     | L2 (software)               | L3 (system)                |
|------------------|------------------------------|-----------------------------|----------------------------|
| small-io         | buffer reads, batch writes   | ROMIO cb_buffer, HDF5 chunks| lfs setstripe -S 4m        |
| metadata         | cache stat(), open once      | H5Pset_coll_metadata_write  | lfs mkdir -c N (DNE)       |
| sequentiality    | sort indices, posix_fadvise  | romio_ds_read=enable        | blockdev --setra 4096      |
| shared-file      | independent file per rank    | romio_cb_read=enable        | increase OST count         |


## Optimization Metric Selection Guide

Use this when the agent must suggest which metric to optimize:

| Dominant bottleneck(s)              | Recommended metric | Rationale                        |
|-------------------------------------|--------------------|----------------------------------|
| read_time_pct or write_time_pct ≥4  | time               | Latency is the binding constraint|
| read_bw_mean or write_bw_mean low   | bandwidth          | Throughput is the binding limit  |
| small_io_pct or rand_pct ≥4         | iops               | Operation rate limits throughput |
| metadata_time_pct ≥4                | metadata_ops       | Metadata is the bottleneck       |
| multiple ≥4                         | time (default)     | Most general; addresses all      |


## Common Parameter Recommendations (from WisIO + Drishti)

- **Buffer size:** 4 MB aligned to Lustre stripe size (1 MB default stripe)
- **ROMIO collective buffer:** 64 MB (`cb_buffer_size=67108864`)
- **Lustre stripe count:** 4–8 for files < 10 GB; 16+ for very large files
- **Linux readahead:** 2–8 MB (`blockdev --setra 4096` to `16384`)
- **DataLoader workers:** `cpu_count // 2` with `prefetch_factor=4`
- **PyTorch checkpoint:** async save in background thread; use lz4 compression
- **vm.dirty_ratio:** 10–20% for write-heavy; 5–10% for checkpoint workloads


## Artifact Files Produced by optimize.yaml

| File                        | Contents                                     |
|-----------------------------|----------------------------------------------|
| `optimization_context.json` | Metric, bottlenecks, papers — shared context |
| `opt_l1_results.json`       | L1 proposals, status, files modified         |
| `opt_l2_results.json`       | L2 proposals, env vars applied               |
| `opt_l2_env.sh`             | Source before each run to keep L2 settings   |
| `opt_l3_results.json`       | L3 changes + rollback commands               |
| `optimization_papers.json`  | Full literature search results               |

All artifact files above live under the current iteration's own `opt<n>/`
run directory (`opt<n>/source/`, `opt<n>/patches/`, `opt<n>/scripts/`,
`opt<n>/traces/{raw,compact}/`) — never a hand-built path like
`ws/opt_results/`. **Strict rule (see dftracer-cheatsheet S0):** before
starting a new iteration and again after applying L1/L2/L3 changes, call
`session_validate_structure(run_id=RUN_ID)`; if `clean=false`, call
`session_reorganize_structure(run_id=RUN_ID, dry_run=False)` before
re-running the smoke test or collecting traces for that iteration.


## Built-in Citations

These references are always available and must be used when their coverage matches the bottleneck being addressed.

### GLANCED-IO (Sinurat et al., HPDC 2026)

- **Authors:** Sinurat et al. (Argonne, U. Chicago, LLNL)
- **Title:** "GLANCED-IO: Taming I/O Optimization for Deep Learning at Scale"
- **Venue:** HPDC '26
- **Local PDF:** `resources/papers/HPDC26_GLANCED_IO.pdf` (search via `search_local_resources` / `rag_search` in dftracer_papers)
- **Covers:** compute/IO overlap verification, num_workers → prefetch_factor → dataset_access_pattern → PFS_striping → transfer_size (portable-to-hard parameter ordering), cross-layer siloed-optimization pitfalls
- **Key rule (OVERLAP-1):** verify dataloader and compute events actually overlap in the dftracer timeline — sequential load→compute→load (low overlap %) signals a pipeline stall even when num_workers/prefetch_factor look correctly configured; the root cause is often a lower layer (small-I/O access pattern, wrong filesystem, unmatched striping), not the app-level DataLoader knobs

### WisIO (Yildirim et al., ICS 2025)

- **Authors:** Izzet Yildirim, Hariharan Devarajan, Anthony Kougkas, Xian-He Sun, Kathryn Mohror
- **Title:** "WisIO: Automated I/O Bottleneck Detection with Multi-Perspective Views for HPC Workflows"
- **Venue:** Proceedings of the 39th ACM International Conference on Supercomputing (ICS 2025), pp. 749–763
- **URL:** https://dl.acm.org/doi/10.1145/3721145.3730395
- **Covers:** small_io, rand/seq (sequentiality), read_time, write_time, metadata_time, fetch_pressure, epoch_straggler

### Drishti (Bez et al., PDSW 2022)

- **Authors:** Jean Luca Bez, Hammad Ather, Suren Byna
- **Title:** "Drishti: Guiding end-users in the I/O optimization journey"
- **Venue:** 2022 IEEE/ACM International Parallel Data Systems Workshop (PDSW), pp. 1–6. IEEE, 2022
- **URL:** https://ieeexplore.ieee.org/document/10027503
- **Covers:** small-io buffering, metadata caching, sequentiality hints, collective I/O (maps to L1/L2/L3 suggestion layers)


## Metric to Optimization Goal Mapping

Use this table to translate the chosen metric into the dftracer analysis fields that must improve:

| Metric        | Goal                        | Target dftracer fields                                          |
|---------------|-----------------------------|-----------------------------------------------------------------|
| `time`        | Minimize I/O latency        | `read_time_pct`, `write_time_pct`, `metadata_time_pct`         |
| `bandwidth`   | Maximize throughput         | `read_bw_mean`, `write_bw_mean`, `seq_pct`                     |
| `iops`        | Maximize I/O operations/sec | `small_io_pct`, `rand_pct`, `intensity_mean`                   |
| `metadata_ops`| Reduce metadata overhead    | `metadata_time_pct`, `metadata_time_frac_parent`               |

When multiple bottleneck scores are >= 4, default to `time` as the most general metric that addresses all dimensions.


## L1 Application Strategies

These are source-code-level changes (applied under `<WS>/annotated/`). No system or middleware config changes at this layer.

### MANDATORY: never change the app's actual I/O access pattern (standing rule, 2026-07-10)

**Do not propose flipping a benchmark/app's access-pattern config (e.g. `MEM_PATTERN`/
`FILE_PATTERN` INTERLEAVED→CONTIG, random→sequential, etc.) just because the alternate pattern
measured faster elsewhere in the same session.** That changes what the workload actually does —
it is not an optimization of the system's ability to serve the REAL pattern, and it can silently
break application logic/correctness (the access pattern is often load-bearing for what the app
computes, not an arbitrary benchmark knob). A finding like "write (INTERLEAVED) is slower than
read (CONTIG)" is a valid DIAGNOSIS, not license to rewrite write's config to be CONTIG.

Instead, keep the access pattern fixed and find ways to make THAT pattern faster:
- **Buffering** — coalesce/batch small or scattered I/O into fewer, larger operations without
  changing the logical order/shape of the accesses (see small_io section below).
- **Caching** — keep hot data resident (OS page cache tuning, app-level LRU cache, HDF5 chunk
  cache `H5Pset_cache`/`H5Pset_chunk_cache`) so repeated/overlapping accesses under the same
  pattern don't re-fetch from the backing store.
- **Prefetching** — issue reads/writes ahead of when the app logically needs them (asynchronous
  I/O / HDF5 async VOL, `posix_fadvise(POSIX_FADV_WILLNEED)`, background threads) so the same
  pattern overlaps with compute instead of blocking it.
- **Stage-in / stage-out** — move data to faster intermediate storage (node-local NVMe, burst
  buffer / Rabbit on Tuolumne, `/dev/shm`) before/after the real I/O phase, without altering the
  pattern the app itself issues against that staged copy.
- **Collective I/O / aggregator tuning** — ROMIO `cb_nodes`, `romio_cb_write`/`romio_ds_write`,
  `CRAY_CB_NODES_MULTIPLIER` — changes HOW the same logical accesses are physically aggregated
  and dispatched, not what the app logically does.
- **Metadata/layout tuning** — `H5Pset_meta_block_size`, chunked vs. contiguous HDF5 storage
  layout (when layout, not access pattern, is the tunable), paged file-space strategy.
- **Filesystem layout** — Lustre striping (`lfs setstripe`) matched to the (unchanged) access
  pattern's transfer size and concurrency.

Before proposing an L1 lever, search the literature specifically for buffering/caching/
prefetching/stage-in optimization techniques for the OBSERVED pattern (see `dftracer-optimizer`
agent's paper-search tools) rather than reaching for a pattern swap. See
[[dftracer-optimization-kb]] Rule 5 for the enforcement of this at record time.

**This explicitly includes transfer-size / request-size sweeps (re-affirmed 2026-07-10).**
Re-running a benchmark with a larger `-t`/transfer-size/block-size than the app or user actually
issues is an access-pattern change, not a system optimization — the same category as an
INTERLEAVED→CONTIG flip. "4 MB transfers get more bandwidth than 4 KB transfers" is a valid
DIAGNOSTIC characterization (it bounds how much headroom the small-request pattern is leaving on
the table), but it must never be reported as the "best config" or "the optimization" — the actual
best config must keep the workload's real request size and instead apply one of the techniques
above (ROMIO two-phase/collective buffering to coalesce many small requests into fewer physical
I/Os, HDF5 chunk/page caching, OS readahead, burst-buffer/node-local stage-in) to serve THAT
request size faster.

### small_io / small_read / small_write  (metric: bandwidth or iops)

- Coalesce small reads into a single larger read with a staging buffer
- Replace per-element writes with batch writes to a pre-allocated buffer
- Use `readv`/`writev` (scatter/gather) instead of loops of `read`/`write`
- For Python: increase `chunk_size` in file iteration; use `np.fromfile` over loops

### rand / low seq_pct  (metric: bandwidth or time)

- Reorder access so items are read in file-offset order (sort indices before I/O)
- Add `posix_fadvise(fd, 0, 0, POSIX_FADV_SEQUENTIAL)` before sequential reads
- Use `mmap` for random access patterns to let the OS handle page faults
- For deep learning: sort dataset by file size, shuffle only indices in memory

### read_time / write_time  (metric: time)

- Pre-open files and keep file descriptors open across iterations
- Use `O_DIRECT` for cache-bypassing when data reuse is low
- Add async I/O: `io_uring` (C/C++) or `aiofiles`/`asyncio` (Python)
- Pre-allocate file size with `fallocate` before writing to avoid fragmentation

### metadata_time  (metric: metadata_ops)

- Cache `stat()` results; avoid repeated `lstat`/`stat` on the same paths
- Open files once per epoch, not once per sample
- Replace `readdir()` loops with pre-built file-list caches
- For Python: replace `os.path.exists()` in hot loops with `try`/`except`

### fetch_pressure / epoch_straggler  (metric: time or bandwidth — deep learning)

- Increase DataLoader `num_workers` (recommend: `cpu_count // 2`)
- Set `prefetch_factor=4` (PyTorch >= 1.7) or use `tf.data.AUTOTUNE`
- Pre-fetch next batch while compute runs the current one
- Use `persistent_workers=True` to avoid worker respawn overhead

### intensity / checkpoint  (metric: time)

- Write checkpoints asynchronously (background thread or process)
- Use memory-mapped files for checkpoint buffers before flushing to disk
- Compress checkpoints in-memory before writing (lz4 is fast enough)


## L2 Software/Middleware Strategies

These are configuration-level changes: environment variables, library hint files, and runtime config. No source code changes. All changes must be reversible (document the original value).

### MPI-IO / ROMIO  (bottleneck: small_io or rand_pct high — metric: bandwidth or time)

- Enable collective buffering: `ROMIO_HINTS=cb_buffer_size=67108864;romio_cb_read=enable`
- Set data sieving: `romio_ds_read=enable;romio_ds_write=enable`
- Increase aggregator count: `cb_nodes=<num_nodes>`
- Write a hints file at `<WS>/romio_hints.txt` and set the `ROMIO_HINTS` env var to its path

### HDF5  (bottleneck: small_io high — metric: bandwidth or iops)

- Set chunk dimensions to align with collective I/O buffer (e.g. 1 MB chunks)
- Enable HDF5 collective metadata: `H5Pset_coll_metadata_write`, `H5Pset_all_coll_metadata_ops`
- Set chunk cache: `H5Pset_cache(fapl, 0, 521, 64*1024*1024, 1.0)`
- For parallel HDF5: set PHDF5 alignment (`H5Pset_alignment`) to stripe size

### HDF5 metadata  (bottleneck: metadata_time high — metric: metadata_ops)

- Disable HDF5 metadata cache evictions during write-heavy phases
- Batch attribute writes with `H5Pset_object_track_times(False)`

### NetCDF / PnetCDF

- Set `nc_set_default_format(NC_FORMAT_NETCDF4_CLASSIC)` for better performance
- Enable collective I/O: `nc_var_par_access(ncid, varid, NC_COLLECTIVE)`

### PyTorch / Deep Learning  (bottleneck: fetch_pressure or small_io high — metric: time or bandwidth)

- DataLoader: `num_workers = cpu_count // 2`, `prefetch_factor = 4`
- DataLoader: `pin_memory = True` (when GPU is present)
- DataLoader: `persistent_workers = True` (avoids respawn per epoch)
- For datasets: use `worker_init_fn` to pre-open file handles per worker
- `PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128` (reduces alloc fragmentation)

### PyTorch epoch stragglers  (bottleneck: epoch_straggler high — metric: time)

- Sort dataset by sample size before training to reduce tail latency per batch
- Use `DistributedSampler` with `shuffle=False`; pre-shuffle indices once

### PyTorch checkpoints  (bottleneck: checkpoint high — metric: time)

- `torch.save` with `_use_new_zipfile_serialization=False` for faster writes
- Async checkpoint: `torch.save` in a separate thread
- Set `TORCH_HOME` to a fast local scratch directory

### TensorFlow

- `tf.data.AUTOTUNE` for prefetch buffer sizes
- `tf.data.experimental.AUTOTUNE` for interleave `cycle_length`
- Set `TF_CUDNN_USE_AUTOTUNE=1`, `TF_GPU_THREAD_MODE=gpu_private`

### General I/O libraries  (bottleneck: rand_pct high — metric: bandwidth)

- Set POSIX readahead: `POSIX_FADV_SEQUENTIAL` via `posix_fadvise` (env-level wrapper)
- Configure library-level read-ahead buffer (if applicable)

### Environment variable wrapper script (general)

- `OMP_NUM_THREADS`, `MKL_NUM_THREADS`: set to physical core count for I/O threading
- `MALLOC_ARENA_MAX=2`: reduce glibc arena fragmentation for I/O-heavy apps
- `HBWMALLOC_POLICY=transparent`: use high-bandwidth NUMA memory when available
- Collect all L2 env vars in `<WS>/opt_l2_env.sh`; source before every run


## L3 OS/Filesystem Strategies

These are OS- and filesystem-level tuning changes. Many require `sudo` or storage-admin access. Every proposal MUST include a rollback command and a privilege classification (`no-sudo | sudo | admin-only`).

**MANDATORY: Before proposing any L3 strategy, confirm the detected FS_TYPE
(see Step 8-PRE of [[dftracer-pipeline]]). Only propose strategies whose
"Valid FS_TYPE" column matches the detected filesystem. Strategies for the
wrong filesystem are silently harmful — e.g., `romio_ds_write=disable` causes
a 4× write regression on VAST NVMe but is safe on Lustre spinning disk.**

### Lustre parallel filesystem  (FS_TYPE: lustre only — bottleneck: any_io or bandwidth — metric: bandwidth or iops)

- **Stripe count** (no sudo, per-directory):
  `lfs setstripe -c <N> <WS>/traces/`
  Rollback: `lfs setstripe -c 1 <WS>/traces/`
  Recommendation: N = OST count, capped at 8 for files < 1 GB; 16+ for very large files
- **Stripe size** (no sudo):
  `lfs setstripe -S 4m <WS>/traces/`  (4 MB aligns to ROMIO collective buffer)
  Rollback: `lfs setstripe -S 1m <WS>/traces/`
- **OST pool** (no sudo, if NVMe/SSD pool available):
  `lfs setstripe --pool flash <WS>/`
- **Per-file stripe before write** (no sudo):
  `lfs setstripe -c 4 -S 4m <WS>/traces/<file>` before writing

### Lustre metadata  (FS_TYPE: lustre only — bottleneck: metadata_time high — metric: metadata_ops)

- Disable file creation time tracking: `lfs setstripe --mdt-count 1` (for small dirs)
- Use DNE (Distributed Namespace): `lfs mkdir -c <N>` (admin-only)
- Client-side: mount with `-o localflock` to reduce lock traffic (requires remount — admin)

### Near-node flash accelerators (Rabbit)  (FS_TYPE: any network PFS — bottleneck: read_time/write_time/bandwidth from network-Lustre latency — metric: bandwidth or time)

On systems with node-local NVMe accelerators, stage hot data onto a per-node or
per-chassis flash tier instead of accessing network Lustre directly. No sudo —
provisioned per job via the scheduler.

- **Tuolumne (Rabbit):** request via Flux `-S "#DW jobdw type=<xfs|gfs2|lustre> ..."`.
  Pick the tier by data-sharing scope (SHM ≤20% node mem → XFS single-node ≤1 TB
  → GFS2 across ≤16 nodes on one chassis with `--coral2-chassis=1` → Lustre for
  >16 nodes). Stage input onto `$DW_JOB_*` once, run against it, copy persistent
  outputs back to Lustre at job end. The `#DW` directive is an **allocation-time**
  flag — ask the user to `flux alloc` with it and report the JOBID before you can
  proxy in. Full guide: [[system-tuolumne-rabbit]].


### Linux kernel readahead  (FS_TYPE: local_nvme, local_hdd only — bottleneck: read_time or seq_pct low — metric: time or bandwidth)

- **Per-device** (sudo):
  `sudo blockdev --setra <KB> /dev/<dev>`
  Default: 128 (64 KB); recommended: 4096–16384 (2–8 MB)
  Rollback: `sudo blockdev --setra 128 /dev/<dev>`
  Side effect: affects all processes reading from this device
- **Per-process** (no sudo — cross-layer L1 reinforcement):
  `posix_fadvise(fd, 0, 0, POSIX_FADV_SEQUENTIAL)` in source code
- **Persistent** (sudo): `/etc/udev/rules.d/60-readahead.rules`

### VM and page cache tuning  (FS_TYPE: local_nvme, local_hdd — bottleneck: write_time or checkpoint high — metric: time)

- `sudo sysctl -w vm.dirty_ratio=20`
  (flush dirty pages when 20% of RAM is dirty; tune to 10–40)
  Rollback: `sudo sysctl -w vm.dirty_ratio=20`
- `sudo sysctl -w vm.dirty_background_ratio=5`
  (start background flush at 5%; reduces write stalls; default 10)
- `sudo sysctl -w vm.dirty_expire_centisecs=3000` (30 s dirty page lifetime)
- `sudo sysctl -w vm.dirty_writeback_centisecs=500` (flush every 5 s)
- Side effect: affects all dirty-page behavior on the node

### VM page cache (read-heavy)  (FS_TYPE: local_nvme, local_hdd — bottleneck: read_time high, data fits in RAM)

- `sudo sysctl -w vm.vfs_cache_pressure=50`
  (reduce kernel tendency to reclaim page cache; default 100)
  Rollback: `sudo sysctl -w vm.vfs_cache_pressure=100`

### I/O scheduler  (FS_TYPE: local_nvme, local_hdd only — bottleneck: rand_pct high on SSD/NVMe — metric: iops or time)

- Check current scheduler: `cat /sys/block/<dev>/queue/scheduler`
- For NVMe (no scheduler overhead): `echo none | sudo tee /sys/block/<dev>/queue/scheduler`
- For HDD: `echo mq-deadline | sudo tee /sys/block/<dev>/queue/scheduler`
- Rollback: `echo <original_scheduler> | sudo tee /sys/block/<dev>/queue/scheduler`

### NUMA memory binding  (FS_TYPE: all — bottleneck: fetch_pressure or intensity high — metric: time or bandwidth)

- Check topology: `numactl --hardware`
- Pin process to NUMA node with local memory (no persistent side effect):
  `numactl --cpunodebind=0 --membind=0 <run_command>`
- For MPI: `mpirun --map-by numa:pe=<cores_per_node>` (OpenMPI)

### Network filesystems (NFS, GPFS, BeeGFS)  (FS_TYPE: nfs | gpfs | beegfs only — bottleneck: read_time or metadata_time high)

These typically require storage admin action — surface as MANUAL RECOMMENDATIONS only.
Do NOT apply these to lustre, vast, or local filesystems.
- NFS: mount with `rsize=1048576,wsize=1048576,async`
- GPFS: `mmchattr -r pagePool=<size>` (admin tool)
- BeeGFS: `beegfs-ctl --settuning --clientNumWorkerThreads=16` (admin)


## Citation Rule

Every optimization proposal presented to the user MUST include a verifiable citation with a URL (arXiv link, DOI, or stable webpage). Follow this search order for each unique bottleneck type:

1. Check `PAPERS` from `optimization_context.json` first (already fetched during pipeline setup).
2. Check `context_opportunities.json` from `session_search_optimization_context` — exhaustive,
   stack-wide, local-first search already run once per iteration (see "Start from what is
   already known" above); its `opportunities`/`benchmark_targets` are already citation-backed.
3. Local retrieval: `dftracer__rag_search(query="<bottleneck_type> optimization", bottleneck=..., system_config=...)`
   — semantic + lexical ranking over `.dftracer_agents/resources/`, free, no network.
4. Search arXiv: `dftracer__search_arxiv(query="<bottleneck_type> optimization HPC parallel I/O MPI", max_results=5, category="cs.DC")`
5. Search Semantic Scholar / combined (7 sources): `dftracer__search_papers_combined(query="<bottleneck_type> I/O tuning parallel filesystem", max_results_each=5)`
6. For any promising arXiv hit, fetch the full record: `dftracer__get_arxiv_paper(paper_id="<arxiv_id>")`

For every citation record:
- authors, title, venue/journal, year
- URL: arXiv link, DOI, or page URL (e.g. `https://arxiv.org/abs/XXXX.XXXXX`)
- the specific finding or section that supports the proposed change (1–2 sentences)

**If no verifiable citation with a URL can be found for a proposal, that proposal MUST be skipped.** Do not present uncited proposals to the user. Instead, write "no L\<N\> fix — no citation available" for that bottleneck.

---

## Start from what is already known — then search exhaustively, not just the bottleneck

Before proposing anything, load [[dftracer-optimization-kb]] and call:

```
opt_kb_lookup(system=<system>, workload=<app>, software="hdf5,mpi-io,lustre")
session_search_optimization_context(run_id=<run_id>, system=<system>, workload=<app>)
```

`opt_kb_lookup` returns MEASURED cross-session results, partitioned into
system-centric (L3), software-centric (L2) and workload-centric (L1) findings,
each with its citation, before/after numbers and caveats. Scope decides
transferability: a system finding does not leave its machine; a workload finding
does not leave its app; software findings travel across both.

`session_search_optimization_context` goes further than the per-bottleneck
searches below: it searches **every software/system layer this session actually
detected** (MPI impl, HDF5, ROMIO, Lustre, ML frameworks — not just the metric
tied to the current diagnosis), local-first (`opt_kb_lookup` + `rag_search` over
`.dftracer_agents/resources/`, free, no network) before any remote fan-out across
7 paper sources. It also runs a query class nothing else in this pipeline does:
**benchmark-target search** — published achieved bandwidth/throughput numbers at
this scale/filesystem, written to `context_opportunities.json` as
`benchmark_targets`. Read the snippets yourself to pull out the actual number —
the tool deliberately does not parse numbers out of free text; that judgment call
belongs to you.

Then render proposals with `opt_proposal_table` (uncited proposals are rejected),
apply **one at a time**, measure, and `opt_kb_record` every result — including
no-ops and regressions, and including `metric_scope`/paired app-metric for any
system-level result (see the non-degradation guard above). Finish with
`opt_kb_render` to publish into the KB skill.


---

## Context economy: query the graph, don't read the tree

Before any step that would open source files, use the `graphify` knowledge graph
(project dependency `graphifyy`, CLI `graphify`):

```bash
graphify query "<target>" --budget 1200   # locate: NODE <sym> [src=file loc=Lnn]
graphify explain <symbol>                 # definition + callers/callees
graphify affected <symbol> --depth 2      # blast radius before you change it
graphify update .                         # refresh after edits (~4s, no LLM)
```

Measured on this repo: locating cost 986 tokens vs 29,456 to read the three
relevant files (3.3%). Run `affected` before editing any shared function and
state the blast radius. Use the CLI, never `graphify-mcp` — its extra tool
schemas would sit in context permanently. See [[dftracer-context-economy]].

## Optimization axes for deep-learning workloads (sweep in this order)

1. **Overlap compute and I/O.** `dataloader_num_workers>0`, `persistent_workers=True`,
   `prefetch_factor`, async checkpointing. Cheapest, usually the biggest win.
   (Mohan et al., *Analyzing and Mitigating Data Stalls in DNN Training*, VLDB 2021,
   https://arxiv.org/abs/2007.06775)
2. **Pinned memory + CPU core affinity — as ONE change.** `pin_memory=True` only pays off when
   each rank is bound to all cores of its GPU's die. Pinned to a single core, the copy thread
   contends with dataloader workers and the benefit inverts. On an APU (e.g. AMD MI300A) CPU and
   GPU share the die and HBM, so affinity determines memory locality, not just scheduling.
   (PyTorch memory-pinning docs, https://docs.pytorch.org/docs/stable/data.html#memory-pinning)
3. **File layout: minimize the NUMBER of reads and metadata calls.** Per-sample small files cause
   an open/stat/close storm on the metadata server. Shard into few large files with an index.
   (Devarajan et al., *DLIO: A Data-Centric Benchmark for Scientific Deep Learning Applications*,
   CCGrid 2021, https://ieeexplore.ieee.org/document/9499416)
4. **System utilization.** PFS bandwidth (striping; Data-on-MDT for small files) and memory
   bandwidth. Establish whether you are bandwidth- or compute-bound before tuning kernels.
   (Williams et al., *Roofline: An Insightful Visual Performance Model*, CACM 2009,
   https://doi.org/10.1145/1498765.1498785)
5. **Compute last.** Mixed precision, kernel/library tuning, then algorithmic change.

**Async checkpointing** is only a win when checkpoint write time is a real fraction of epoch
time — verify first. (Mohan et al., *CheckFreq*, USENIX FAST 2021,
https://www.usenix.org/conference/fast21/presentation/mohan; Eisenman et al., *Check-N-Run*,
USENIX NSDI 2022, https://www.usenix.org/conference/nsdi22/presentation/eisenman)

**Guard rail.** A wall-clock gain from writing fewer checkpoints, reading less data, or running
fewer epochs is *doing less*, not going faster. Check event and byte counts before crediting it.

**Measurement methodology, reconfirmed 2026-07-24 (IOR 4.0.0, Tuolumne, HDF5 1.14.5):**
- **Tracing state must be identical across every arm of an A/B.** A baseline captured with
  `DFTRACER_ENABLE=1` (FUNCTION mode) compared against untraced optimization-variant runs
  looked ~60% faster in the variants purely from removing tracing overhead (FUNCTION-mode
  cost was ~37% of apparent write bandwidth at 4 KiB transfers on this workload) — nothing to
  do with the optimization under test. Before crediting any delta, grep both arms' launch
  scripts for `DFTRACER_ENABLE`/`DFTRACER_INIT` and refuse the comparison if they differ.
  Report headline bandwidth from untraced runs only (trace collection is a separate,
  intentionally-instrumented pass, not the number the optimization claim is made against).
- **Every replicate must write to a fresh, unused directory, and arms must be interleaved**
  (v0, v1, v0, v1, ... not v0×5 then v1×5). Filesystem/cache state drifted ~11% between two
  back-to-back same-night campaigns on Lustre — larger than most of the optimization effects
  under test. Back-to-back-by-arm ordering biases later arms toward warmer cache/layout state.
  A candidate delta is only claimable when its full range does not overlap the baseline's.

**General rule, confirmed on 1000genome-workflow `individuals.py` (2026-07-26):** before
reaching for an FS-layer lever (striping, ROMIO), check for a `tarfile.add(dir)` /
`tarfile.open()...write-then-reopen` pattern following a many-small-file producer loop --
compressing/archiving a directory that was just written re-touches every file a second
time (open+stat+read+close per member), doubling the effective op count for no new logical
data. Streaming members directly into the tar as they're produced (`tarfile.addfile(TarInfo,
BytesIO)` in-memory, or writing straight into the open `TarFile` instead of scratch files)
removes the round trip entirely. Measured on this workload: `IND_TAR_STREAM=1` alone was
-13.2% standalone, and its relative share of the remaining cost GREW (not shrank) once a
companion compute fix collapsed the parse-time component -- see workload-1000genome.
