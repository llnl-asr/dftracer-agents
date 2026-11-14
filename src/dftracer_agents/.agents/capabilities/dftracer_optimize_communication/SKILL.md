---
name: dftracer_optimize_communication
description: Communication component (MPI/collective/network) of the dftracer optimizer - the exhaustive 9-dimension checklist, comm_wait L1/L2/L3 strategies, built-in citations, and a validator for the citation-backed findings table. Load for comm_wait / mpi_wait / collective / sync_time / allreduce bottlenecks, an analyzer reading of "zero communication events", RCCL/NCCL allreduce time missing (HIP tracing off) or hidden in KERNEL_DISPATCH as ncclDevKern (HIP tracing on), Cray MPICH ignoring cb_nodes, a scheduler-owned MPI binary (Pegasus PMC) with no MPI events, low worker utilization in a DAG workflow, or NCCL_ALGO/NCCL_PROTO tuning questions.
---
Cross-references: [[dftracer_optimize]], [[dftracer_opt_kb]], [[dftracer_papers]], [[dftracer_optimize_io]], [[dftracer_optimize_compute]], [[dftracer_optimize_memory]], [[dftracer_analyze]], [[dftracer_diagnose]], [[software-mpi]], [[software-rocm]]

# dftracer_optimize_communication

Communication-component sibling of `dftracer_optimize_io`. The metric key used by the MCP
optimization tools is `comm_wait` (see `_L1_STRATEGIES`/`_L2_STRATEGIES`/`_L3_STRATEGIES["comm_wait"]`
in `dftracer_agents/mcp_tools/tools/optimizations/strategies.py`); related classification keys:
`comm`, `mpi_wait`, `collective`, `sync_time`, `allreduce`. `opt_communication_checklist` renders
the checklist, those strategies and the default citation as data; `dftracer_optimize_communication_check`
rules on the findings table.

## Rules
- Load first, this skill is the rulebook: the checklist below (metric `comm_wait`), `software-mpi`
  (MPI/ROMIO env-var wiring, Cray MPICH quirks: `CRAY_CB_NODES_MULTIPLIER` vs standard `cb_nodes`,
  `flux proxy` env propagation) and the system skill / system detection (actual network fabric,
  interconnect and launcher flux/slurm) before proposing transport-layer tuning.
- Skills first, and every question asked and answer given is a lesson (user instruction 2026-07-28/29):
  run knowledge/skill search before acting, record user answers as lessons as you go, propagate
  generic rules everywhere.
- Load your plan section first: read `pipeline_plan.md` (`session_read_file(run_id, subfolder=".",
  filepath="pipeline_plan.md")`, fall back to `subfolder="scripts"`), follow the `## STEP N:
  dftracer-optimizer-communication` section verbatim; if it is missing or contradicts the dispatch
  inputs, report back instead of guessing.
- Tool-first (MANDATORY), in this order: `opt_kb_lookup` (what was already measured for THIS
  component) -> `session_search_optimization_context` (exhaustive, stack-wide, local-first, step "0.5"
  before proposal generation, every iteration) -> `session_generate_optimization_proposals` ->
  `session_optimize_l1_app` / `session_optimize_l2_software` / `session_optimize_l3_filesystem`
  with metric key `comm_wait` -> `session_optimization_iteration` -> `comparator` ->
  `session_search_optimization_papers` / `rag_search` -> `search_arxiv` / `search_semantic_scholar`.
  If the tools are not available, stop and ask the user to start the dftracer MCP server; if they
  error, fix the tool or its wiring before custom Bash. The final report separates **TOOL FINDINGS**
  from **MANUAL ANALYSIS** in a table and labels every finding with its source.
- Independent literature/KB search pass, every iteration, not diagnosis-gated: (1) KB recall
  `opt_kb_lookup(system, workload, software)`; (2) literature search (`search_arxiv`,
  `search_semantic_scholar`, `session_search_optimization_papers`, `rag_search`) for techniques matching
  the observed communication topology/collective shape, even when diagnosis does not top-rank it. In
  addition to, never instead of, diagnostic proposals; every literature-sourced technique tried, pass
  or fail, is `opt_kb_record`ed with its citation and a `notes` tag ("literature-pass finding, not
  diagnosis-ranked").
- Exhaustive dimension checklist (MANDATORY, every invocation, user instruction 2026-07-10): walk all 9
  dimensions below and classify EACH: **Applied & measured** (result + citation), **Applicable, not
  measured** (concrete reason: budget/time, superseded by a stronger same-layer candidate), or **Not
  applicable** (concrete technical reason: wrong hardware/system, no literature found, structurally
  inert, admin-only/already maxed per the system skill). Never silently omit a category; run the
  literature search per category before marking it not applicable ("I didn't look" is never a reason).
- Every row of the resulting `opt_proposal_table` MUST carry `app_impact_pct` (% of current app wall
  time, measured or bounded estimate) and `system_impact_pct` (throughput/bandwidth/utilization effect,
  0 if not measured/applicable); columns `#`, `Strategy`, `Description of Optimization`, `App Impact`,
  `System Impact`, `Weighted Score` (50/50, sorted descending by the tool). Never omit these fields.
- Ordering: communication is optimized SECOND in the canonical I/O -> communication -> memory ->
  compute order (communication-bound stalls often mask memory/compute behavior underneath), and
  L1 -> L2 -> L3 within the component.
- The optimization loop (MANDATORY order): Step 1 recall then search exhaustively
  (`opt_kb_lookup`, `session_search_optimization_context(run_id, system, workload)`, which also writes
  benchmark-target numbers to `context_opportunities.json`: read the snippets to extract the number;
  read KB `notes`, where caveats like "accepted but ignored" live). Step 2 propose as a citation-backed
  table with `opt_proposal_table` showing the WHOLE option space (every checklist candidate,
  applied / not-applied with a one-line reason). Step 3 apply ONE change, re-run at the SAME scale,
  measure, `opt_kb_record` (no-ops and regressions too; scope honestly: machine -> system, library ->
  software, application -> workload). Step 4 `opt_kb_render()`.
- VALIDATE every applied optimization by re-running and comparing: identical message count/volume with
  lower communication wait time = a real, safe win.
- Replicates (MANDATORY): every iteration (baseline, each candidate, final best) has >= 5 replicates
  before `comparator` or `opt_kb_record`; CV > ~10-15% -> 8, then 10 replicates; compare p50 and state
  p95/min/max; `opt_kb_record` before/after are medians with the band and replicate count in `notes`.
- metric_scope axis: `app` (default) or `system`; a `system` record carries the paired
  `app_metric`/`app_before`/`app_after`; `guard_triggered: true` (app regressed > 2%) is treated as
  REGRESSED: revert, never keep a system win that cost the app.
- Allocation rules: ask for the active allocation id, verify it is `R` with `flux jobs`, run with all
  planned nodes and `--exclusive` (`flux proxy <alloc_id> flux run -N <n> -n <tasks> --exclusive ...`),
  problem size large enough to resolve comm_wait, data on the system PFS, never compare a smoke run
  against production; the tracer agent does the actual run.
- Run length: at least ~10 minutes of training/steady state and one replicate of baseline and best
  variant; report deltas against the noise band.
- Environment consistency: install env == run env; reuse the app's own scripts verbatim; bind CC/CXX
  to the MPI the app uses; a zero exit code does not mean tracing worked.
- Capture the run record before you finish (`session_capture_run_record`, plus
  `session_snapshot_run_source` with a source_path outside the run's own tree); logs go to
  `<WS>/artifacts/<step>_<what>.log`, never `<WS>/tmp/`.
- Step profiling: bracket the work with `profile_step_begin/end(step="STEP N: dftracer-optimizer-communication")`
  (component-tagged, e.g. "STEP 7c"); never call `profile_bind`.
- Context economy: locate with `graph_query` / `graph_query(mode="docs")` before reading files or
  whole skills; run `mode="affected"` before editing a shared function.
- Pipeline eagerly (user instruction 2026-07-10): start on the ready units, track pending ones, state
  coverage in every report.
- Last step: walk `opt_communication_checklist`, fill one row per dimension, and run
  `dftracer_optimize_communication_check(findings_json)`; hand the table to the orchestrator only on
  `status: pass`.

## Constraints
- Never "do less": no fewer checkpoints, less data, less communication, skipped synchronization or
  verification steps, reduced work/precision/correctness. Make the SAME work faster.
- Never change the app's actual communication pattern as an "optimization": do not restructure WHICH
  ranks talk to which, change a collective's semantics (e.g. replace an `MPI_Allreduce` with a
  functionally different reduction) or drop synchronization points correctness depends on. Keep the
  logical shape fixed; algorithm selection, protocol tuning, topology placement and overlap with
  compute are the safe levers. This covers message-size/batch-size sweeps too (user standing rule
  2026-07-10).
- Never bundle two changes in one measurement.
- Never fall back to manual work when a tool fix needs a server restart: stop, name the tool, wait.
- Self-learning confirmation gate: PROPOSE new pitfalls and the working recipe to the main thread; do
  not write lesson/skill/agent files yourself. Redact before you persist (no usernames, user paths,
  job ids, UUIDs, hostnames).
- Shared source tree with a parallel sibling optimizer (MANDATORY): know which files are yours; pin
  the sibling's known env-var gates (e.g. a compute optimizer's `PECAN_TORCH_COMPILE`) to their safe
  value in your own run script; if a run crashes in a parallel window, check for diffs in files you
  did not touch before blaming your change (real incident: NCCL "remote process exited" twice in one
  session).

## Corner cases
- A "zero communication events" reading can be a threshold artifact: a low-frequency category can be
  real but statistically buried next to a million-event dominant one. Before reporting zero cost,
  count and sum `dur` for its literal `cat` through the trace tools (`event_count` / `view` / `reader`
  in [[dftracer_analyze]], or TraceViewer), never by decompressing traces yourself.
- GPU collective (RCCL/NCCL) allreduce time is invisible with `DFTRACER_ENABLE_HIP_TRACING=OFF`:
  PyTorch DDP fuses the allreduce into `loss.backward()`; flag it as an open coverage gap, never assume
  zero comm or attribute all `model-backward` time to compute.
- With HIP tracing ON, RCCL kernels appear as `cat=KERNEL_DISPATCH` events named `<n>_Z21ncclDevKern...`
  and the Layer Breakdown counts them as COMPUTE. Query `KERNEL_DISPATCH` events whose name contains
  `ncclDevKern` and subtract that time from compute before ranking dimensions.
- Rank/straggler skew vs bandwidth: check the collective kernels' duration percentiles first. A
  bimodal distribution (fast p50, long slow tail) is straggler skew; if < ~20% of collective time is in
  sub-200 ms kernels, NCCL_ALGO/NCCL_PROTO/buffer tuning has almost nothing to act on: balance per-rank
  work (balanced sharding, `drop_last=True`), cite arXiv:2505.23523.
- A scheduler-owned MPI binary that is not annotated (e.g. `pegasus-mpi-cluster`) produces zero MPI
  events though real MPI traffic happens: never report "zero communication" from its absence; use the
  scheduler's own summary (PMC `[cluster-summary ... bytes_sent=<N> ...]`) as ground truth and state the
  coverage gap.
- DAG max-width pre-check: before proposing a worker-pool/rank-count lever for a DAG workflow, compute
  the DAG's level widths from the `.dag` TASK/EDGE structure; if max width < worker count, low
  utilization is a DAG-shape artifact, not a schedulable inefficiency.
- When the bottleneck lives in library code (e.g. reductions inside `libvpic.a`), measuring the fix
  needs an incremental library relink: weigh the rebuild against the remaining allocation; it is fine
  to report the patch + citation as a high-confidence, explicitly unmeasured candidate and defer the
  trial to a dedicated validation step rather than fabricate a result.
- Cray MPICH ignores the standard `cb_nodes` hint; only `CRAY_CB_NODES_MULTIPLIER` raises the MPI-IO
  aggregator count (the validator's component rule flags a bare `cb_nodes`).
- Always check the diagnosed severity/prevalence ranking before spending budget on communication: for
  small-graph GNN DDP workloads (PECAN) I/O dominated by two orders of magnitude.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
`dftracer_optimize_communication_check` reports these checks; repair, re-render with
`opt_communication_checklist`, re-validate. The same failure twice stops the loop for the user.
- findings_parse: the input is not JSON -> pass a JSON list of rows (or `{"rows": [...]}`, or a file path).
- findings_present: no rows -> walk the checklist; one row per dimension.
- every_dimension_has_a_verdict: add the missing dimension numbers with applied / applicable (reason) /
  not_applicable (reason, after the literature search).
- verdicts_valid: use `applied`, `applicable` or `not_applicable`.
- level_is_L1_L2_L3: set `level` on applied/applicable rows (L1 application, L2 middleware, L3 system).
- level_matches_dimension: dimensions 1-2 are L1, 3-5 L2, 6-8 L3, 9 any; move the row or fix its level.
- citation_backed: cite a paper (arXiv/DOI) > docs > web, or `session:<run_id>` for an in-house measurement;
  search with [[dftracer_papers]] if none is at hand.
- reason_for_not_applied: give the concrete technical reason.
- impact_fields: applied rows carry numeric `app_impact_pct` and `system_impact_pct` (0 when N/A).
- never_do_less: drop the row or replace it with a lever that keeps the work.
- component_rules: a pattern/topology rewrite -> keep what is communicated and tune algorithm, protocol,
  transport or placement; a bare `cb_nodes` -> set `CRAY_CB_NODES_MULTIPLIER` on Cray MPICH or mark it
  not applicable.
- If the checklist or rules are wrong (a new dimension, a false positive), fix the tool via
  capability_update, not the input.

## Self-heal
- If a tool here is wrong, capability_update it; do not work around it in shell.
- If an MCP tool needs a server restart after a fix, stop and report rather than redo its job by hand.

## Self-learn
- What is explored here becomes a capability: see capability_builder. New communication findings go to
  LESSONS.md here (component-generic), to the workload/system/software capability when they are scoped
  there, and measured results to [[dftracer_opt_kb]] with `opt_kb_record`.
- Generic deterministic logic (a new check, a new dimension) goes into the tool; judgement stays here.

## What works
- 2026-08-02 (ray_molformer, 16 GPUs, HIP tracing on): attributing `ncclDevKern` KERNEL_DISPATCH time to
  communication revealed 541.7 s of RCCL time (95.4% of the KERNEL_DISPATCH bucket); the analyzer's own
  `comm` (306.9 s) undercounted real communication ~2.8x. Kernel-duration percentiles showed 87% of
  collective time in a slow tail: straggler skew, so the lever is load balance, not NCCL tuning.
- 2026-07-26 (1000genome / Pegasus PMC): the PMC cluster-summary `bytes_sent` (29,956 bytes over a
  316.5 s / 32-rank run) proved communication negligible where traces had no MPI events; DAG width 17
  vs 31 workers explained 19.4% utilization as DAG shape.
- 2026-07-20 (PECAN, 16 ranks): communication was not the bottleneck (`cpu-gpu-transfer` 25.58 s,
  <1% of wall time; DDP overlap and `pin_memory=True` already default).

## Reference: dimension checklist (walk ALL, every session)
1. **L1 overlap communication with compute**: non-blocking collectives (`MPI_Iallreduce`, `MPI_Ibcast`)
   launched early, gradient-ready hooks that start all-reduce before the full backward pass finishes.
2. **L1 message aggregation**: batch multiple small messages into one larger send/recv without changing
   the logical communication topology (same category as I/O's small-request coalescing: coalesce,
   don't restructure).
3. **L2 collective algorithm selection**: topology-aware algorithm choice (`OMPI_MCA_coll_hcoll_enable`,
   MPICH's algorithm-selection env vars) picks tree vs ring vs recursive-doubling by message size/rank
   count, without changing WHAT is communicated.
4. **L2 eager/rendezvous protocol tuning**: eager-limit / rendezvous threshold so small messages use the
   low-latency path and large ones the bandwidth-optimized path.
5. **L2 transport/provider selection**: libfabric/UCX provider (verbs vs ofi vs tcp), RDMA vs TCP
   fallback: check what this system's fabric actually has first (system skill).
6. **L3 topology-aware rank placement**: co-locate frequently-communicating pairs (same node/switch):
   `--map-by`, rankfiles.
7. **L3 network/interconnect tuning**: NIC binding, queue-pair/RDMA depth, congestion control: check
   tunability (often admin-only) first.
8. **L3 collective-buffer sizing at the system level**: `cb_nodes`/`CRAY_CB_NODES_MULTIPLIER` (Cray
   MPICH ignores `cb_nodes` directly, see `software-mpi`) for MPI-IO collective I/O, which is
   communication-shaped.
9. **Compute/communication overlap**: shared with the compute checklist; record once, cross-reference
   from whichever checklist is walked second.

## Reference: strategies (metric comm_wait)
- L1: **Overlap gradient all-reduce with backward-pass compute**: register gradient-ready hooks to launch
  all-reduce as soon as each layer's gradient is computed (Thakur, Rabenseifner, Gropp, IJHPCA 2005).
- L2: **Topology-aware collective algorithm selection**: `OMPI_MCA_coll_hcoll_enable=1` (OpenMPI) or the
  MPICH equivalent, selecting tree/ring/recursive-doubling by message size and topology.
- L3: **Topology-aware rank binding**: co-locate communicating ranks to minimize inter-node hops
  (rankfile, `--map-by numa`).
- L3: **Cray MPICH `CRAY_CB_NODES_MULTIPLIER`**: the only way to raise the aggregator count for MPI-IO
  collective operations on Cray MPICH (see `software-mpi`).

## Reference: built-in citations
- Thakur, R., Rabenseifner, R., Gropp, W., *Optimization of Collective Communication Operations in MPICH*,
  IJHPCA 19(1), pp. 49-66, 2005, https://doi.org/10.1177/1094342005051521
- *Efficient AllReduce with Stragglers*, arXiv:2505.23523 (straggler-skew class of fix).

## Reference: metric to optimization goal
| Metric | Optimization goal |
|---|---|
| `comm_wait` / `mpi_wait` / `collective` / `sync_time` / `allreduce` | Reduce time ranks spend blocked on communication, without changing what/who they communicate with |
