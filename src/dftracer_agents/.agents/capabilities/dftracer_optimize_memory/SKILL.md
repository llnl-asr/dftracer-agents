---
name: dftracer_optimize_memory
description: Memory component (bandwidth/cache/NUMA/allocation) of the dftracer optimizer - the exhaustive 10-dimension checklist, mem_bw L1/L2/L3 strategies, built-in citations, and a validator for the citation-backed findings table. Load for mem_bw / memory / cache_miss / numa bottlenecks, unified-memory APUs (MI300A) where pinned-memory/NUMA/huge-page levers are inert, XLA/framework memory preallocation, gc.disable() proposals for Python loops, shared/mmap model-weight loading proposals, or deciding from node memory counters whether memory is a bottleneck at all.
---
Cross-references: [[dftracer_optimize]], [[dftracer_opt_kb]], [[dftracer_papers]], [[dftracer_optimize_io]], [[dftracer_optimize_compute]], [[dftracer_optimize_communication]], [[dftracer_analyze]], [[software-rocm]]

# dftracer_optimize_memory

Memory-component sibling of `dftracer_optimize_io`. The metric key used by the MCP optimization
tools is `mem_bw` (see `_L1_STRATEGIES`/`_L2_STRATEGIES`/`_L3_STRATEGIES["mem_bw"]` in
`dftracer_agents/mcp_tools/tools/optimizations/strategies.py`); related classification keys:
`memory`, `cache_miss`, `numa`. `opt_memory_checklist` renders the checklist, those strategies and
the default citation as data; `dftracer_optimize_memory_check` rules on the findings table.

**MI300A APU note:** see `software-rocm`'s "unified memory is INTRA-PACKAGE only" and
"`XLA_PYTHON_CLIENT_PREALLOCATE=false`" sections before assuming unified-memory systems make
allocator/pool-reservation levers inert: pinned-memory/NUMA levers are inert there, but framework
preallocation levers can matter MORE, not less, because the reserved pool competes with the CPU side
for the same physical HBM.

## Rules
- Load first, this skill is the rulebook: the checklist below (metric `mem_bw`), the compute checklist
  ([[dftracer_optimize_compute]]: NUMA binding and cache blocking are shared dimensions, so a finding is
  not duplicated as "new" in both), and the system's NUMA topology / memory hierarchy (system skill,
  system detection) before proposing a binding strategy.
- Skills first, and every question asked and answer given is a lesson (user instruction 2026-07-28/29).
- Load your plan section first: `pipeline_plan.md`, `## STEP N: dftracer-optimizer-memory`, verbatim;
  report back if missing or contradictory.
- Tool-first (MANDATORY), in this order: `opt_kb_lookup` -> `session_search_optimization_context`
  (step "0.5", every iteration) -> `session_generate_optimization_proposals` -> `session_optimize_l1_app`
  / `session_optimize_l2_software` / `session_optimize_l3_filesystem` with metric key `mem_bw` ->
  `session_optimization_iteration` -> `comparator` -> `session_search_optimization_papers` /
  `rag_search` -> `search_arxiv` / `search_semantic_scholar`. No tools: ask the user to start the
  dftracer MCP server; tools erroring: fix the tool first. Separate **TOOL FINDINGS** from **MANUAL
  ANALYSIS** in the report.
- Independent literature/KB search pass every iteration (KB recall, then literature search for the
  observed memory access/allocation pattern even when diagnosis does not top-rank it); every
  literature-sourced technique tried is `opt_kb_record`ed with its citation and a "literature-pass" note.
- Exhaustive dimension checklist (MANDATORY, every invocation, user instruction 2026-07-10): walk all 10
  dimensions and classify EACH as Applied & measured / Applicable, not measured (reason) / Not applicable
  (technical reason, after the literature search). Never silently omit a category.
- Cheap first check before the literature search: dismiss the capacity-related categories (1-2, 6-7, 9,
  and gradient checkpointing) from `dftracer_service` node-counter evidence rather than assumption. The
  node-counter trace (`<WS>/traces/service_*.pfw.gz`) emits a 1 Hz `"name":"memory"` counter series with
  the full `/proc/meminfo` field set. Read it through the trace tools ([[dftracer_analyze]]: `view` /
  `reader` with a query on `name == "memory"`, or TraceViewer), never by decompressing traces yourself,
  and take min/max of `MemAvailable` for peak node memory use; also check `SwapTotal`, `Dirty`,
  `Writeback`, `HugePages_Total`. If peak use is a small fraction of total and swap/dirty/writeback are
  all 0, most capacity categories are dismissible at once.
- Every row of the `opt_proposal_table` carries `app_impact_pct` and `system_impact_pct` (0 if N/A);
  columns `#`, `Strategy`, `Description of Optimization`, `App Impact`, `System Impact`,
  `Weighted Score` (50/50, sorted descending). Never omit them.
- Ordering: memory is optimized THIRD in the canonical I/O -> communication -> memory -> compute order:
  after I/O and communication (memory stalls are often masked by, or masking, those larger bottlenecks),
  before compute; verify with a roofline/bandwidth check that the workload is actually
  memory-bandwidth-bound before investing here. L1 -> L2 -> L3 within the component.
- The optimization loop (MANDATORY order): recall + exhaustive search (`opt_kb_lookup`,
  `session_search_optimization_context`, read `context_opportunities.json` snippets and KB `notes`) ->
  citation-backed `opt_proposal_table` showing the WHOLE option space -> apply ONE change at the same
  scale, measure, `opt_kb_record` (no-ops and regressions too, honest scope) -> `opt_kb_render()`.
- VALIDATE every applied optimization by re-running and comparing: identical data volume/lifetime with
  lower memory-bound stall time = a real, safe win.
- Replicates: >= 5 per iteration before comparing or recording; CV > ~10-15% -> 8, then 10; compare
  medians, state p95/min/max; record medians with the band and replicate count in `notes`.
- metric_scope: a `system` record carries the paired app metric; a fired guard (app regressed > 2%)
  means revert.
- Allocation rules, run length (~10 minutes, one replicate of baseline and best), environment
  consistency, run record capture (`session_capture_run_record`, `session_snapshot_run_source`), logs to
  `<WS>/artifacts/`, step profiling (`profile_step_begin/end(step="STEP N: dftracer-optimizer-memory")`,
  e.g. "STEP 7d"; never `profile_bind`), context economy (`graph_query` before reading), eager
  pipelining: as in [[dftracer_optimize_communication]] and [[dftracer_optimize]].
- Last step: walk `opt_memory_checklist`, one row per dimension, and run
  `dftracer_optimize_memory_check(findings_json)`; hand over only on `status: pass`.

## Constraints
- Never "do less" (less data, less memory by doing less work, reduced precision/correctness).
- Never change the app's actual memory footprint semantics as an "optimization": no reducing batch size,
  dropping cached data correctness depends on, or truncating precision. Buffer pooling, in-place ops and
  cache blocking are safe ONLY when the app's logical data lifetime and values are unchanged: verify with
  a byte-identical output check before crediting a result. Covers batch-size/tile-size sweeps too (user
  standing rule 2026-07-10).
- Never bundle two changes; pinned memory and core/GPU-die affinity are separate line items, never one
  attribution.
- Never fall back to manual work when a tool fix needs a server restart.
- Self-learning confirmation gate: propose, do not write skills/lessons/agents yourself; redact before
  persisting.
- Shared source tree with a parallel sibling optimizer: pin the sibling's env-var gates, diff files you
  did not touch before blaming your change.

## Corner cases
- Unified-memory APUs (MI300A): CPU and GPU share one HBM3 pool, removing host<->device staging-copy
  pressure. `pin_memory=True` and other staging-copy levers are default/inert: mark them not applicable
  early citing arXiv 2508.12743. Huge pages / `madvise(MADV_HUGEPAGE)` are a guaranteed no-op on this
  system's glibc (not page-aligned malloc), confirmed via the KB. A workload with no measured
  HBM-bandwidth pressure (data-loading-bound) gets a documented "not memory-bound" verdict.
- `gc.disable()` (+`gc.freeze()`) is the wrong lever for acyclic object graphs: measured +1.7%
  regression on a Python VCF-parsing loop. Confirm the object graph has reference cycles
  (`gc.get_stats()` collection counts, object graph inspection) before proposing it.
- Shared/mmap model weights across N workers has no target when the model is built from config rather
  than loaded from a file: first look for any weight-file read (`.bin`, `.safetensors`, `.pt`, `.pth`,
  `.ckpt`) in the trace's file-name field through the trace tools (`view`/`reader` queries, or
  TraceViewer), never by decompressing traces yourself.
- Shared boundaries: HDF5 chunk cache -> the I/O capability owns it; general allocator arena -> here;
  NUMA binding and cache blocking are shared with compute: record once, cross-reference.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
`dftracer_optimize_memory_check` reports these checks; repair, re-render with `opt_memory_checklist`,
re-validate. The same failure twice stops the loop for the user.
- findings_parse: pass a JSON list of rows (or `{"rows": [...]}`, or a file path).
- findings_present: walk the checklist, one row per dimension.
- every_dimension_has_a_verdict: add each missing dimension (the node-counter check dismisses capacity
  categories cheaply).
- verdicts_valid: `applied`, `applicable` or `not_applicable`.
- level_is_L1_L2_L3: set `level` on applied/applicable rows.
- level_matches_dimension: dimensions 1-3 are L1, 4-7 L2, 8-10 L3.
- citation_backed: cite paper > docs > web or `session:<run_id>`; search with [[dftracer_papers]].
- reason_for_not_applied: concrete technical reason (e.g. MI300A unified memory, not memory-bound per roofline).
- impact_fields: numeric `app_impact_pct`/`system_impact_pct` on applied rows (0 when N/A).
- never_do_less: drop or replace the row.
- component_rules: a footprint change (batch size, model size, working set) -> keep the footprint and
  reuse, pool, place or bind memory instead.
- A wrong rule or dimension is fixed in the tool via capability_update, not in the input.

## Self-heal
- If a tool here is wrong, capability_update it; do not work around it in shell.
- If an MCP tool needs a server restart after a fix, stop and report.

## Self-learn
- What is explored here becomes a capability: see capability_builder. Component-generic memory findings
  go to LESSONS.md here; workload/system/software-scoped ones to that capability; measured results to
  [[dftracer_opt_kb]] with `opt_kb_record`.

## What works
- 2026-08-02 (ray_molformer/MI300A): node-counter memory series showed 22.5 GiB peak of ~490 GiB
  (4.6%), zero swap and spill: capacity categories dismissed in one step, memory not the bottleneck;
  gradient checkpointing would have been a pure regression on an already compute-bound job.
- 2026-08-02 (ray_molformer): no weight-file read across 27.3M events (model built in-process from a HF
  `PretrainedConfig`): shared/mmap weights marked not applicable without a trial.
- 2026-07-20 (PECAN/MI300A): staging-copy and huge-page levers marked not applicable from the
  architecture and the KB; a data-loading-bound workload got a "not memory-bound" verdict.

## Reference: dimension checklist (walk ALL, every session)
1. **L1 buffer reuse/pooling**: reuse allocated buffers across iterations instead of alloc/free churn;
   object/tensor pooling.
2. **L1 in-place operations**: avoid unnecessary intermediate copies (in-place tensor ops, avoiding
   redundant `malloc`+`memcpy` where the app's logic permits it losslessly).
3. **L1 cache blocking / tiling**: restructure the hot loop's memory access order to maximize reuse of
   data already in cache (raises arithmetic intensity; shared with the compute vectorization work).
4. **L2 allocator selection**: NUMA-aware allocator (jemalloc/tcmalloc) via `LD_PRELOAD`, tuned arena count.
5. **L2 pinned memory for host-device transfers**: `cudaMallocHost`/`hipHostMalloc` pinned buffers so DMA
   needs no staging copy; only pays off with correct core/GPU-die affinity (measure both as separate line
   items, never bundle their attribution).
6. **L2 huge pages / TLB pressure reduction**: THP or explicit `madvise(MADV_HUGEPAGE)` for large allocations.
7. **L2 HDF5/MPI-IO internal buffer sizing**: chunk cache size, collective I/O buffer size; record the
   finding once in whichever capability owns the tunable (HDF5 chunk cache -> io; allocator arena -> here).
8. **L3 NUMA memory binding**: `numactl --cpunodebind=<n> --membind=<n>` or `hwloc-bind` (shared with
   compute: record once, cross-reference).
9. **L3 page-cache / VM tuning**: `vm.dirty_ratio`, `vm.vfs_cache_pressure`, huge-page kernel settings:
   check tunability (often admin-only) first.
10. **L3 memory-bandwidth roofline check**: establish that the workload is memory-bandwidth-bound before
    any memory-layer tuning; a compute-bound kernel gains nothing.

## Reference: strategies (metric mem_bw)
- L1: **Increase batch/tile size to raise arithmetic intensity**: restructure the hot loop to reuse loaded
  data across more operations before eviction (cache blocking) (McCalpin 1995). Note: this is a loop
  restructuring (tiling), never a change of the app's batch size semantics (see Constraints).
- L2: **Transparent huge pages / `madvise`**: back large allocations with huge pages to cut TLB misses
  (`MALLOC_MMAP_THRESHOLD_`, `MALLOC_TRIM_THRESHOLD_`).
- L2: **NUMA-aware allocator**: `LD_PRELOAD=libjemalloc.so` with `MALLOC_CONF=narenas:<numa_nodes>`.
- L3: **NUMA binding**: `numactl --cpunodebind=<n> --membind=<n>` (`strategies.py` L3 `mem_bw` entry).
- L3: **Page-cache tuning**: `vm.vfs_cache_pressure`, `vm.dirty_ratio`; admin-only on most HPC systems,
  verify tunability first.

## Reference: built-in citations
- McCalpin, J.D., *Memory Bandwidth and Machine Balance in Current High Performance Computers*, IEEE
  Computer Society TCCA Newsletter, 1995, https://www.cs.virginia.edu/stream/ref.html
- Williams, S., Waterman, A., Patterson, D., *Roofline: An Insightful Visual Performance Model*, CACM
  52(4), 2009, https://doi.org/10.1145/1498765.1498785 (bandwidth- vs compute-bound classification)
- MI300A unified-memory architecture reasoning: arXiv 2508.12743.

## Reference: metric to optimization goal
| Metric | Optimization goal |
|---|---|
| `mem_bw` / `memory` / `cache_miss` / `numa` | Reduce memory-bound stall time / increase effective bandwidth utilization without changing what data the app holds or when |
