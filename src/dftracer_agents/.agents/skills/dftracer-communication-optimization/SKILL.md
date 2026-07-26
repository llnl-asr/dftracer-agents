---
name: dftracer-communication-optimization
description: Communication-component (MPI/collective/network) bottleneck-to-optimization mappings, papers, and L1/L2/L3 strategies for the dftracer optimization pipeline
---

Cross-references: [[dftracer-io-optimization]] [[dftracer-compute-optimization]] [[dftracer-memory-optimization]] [[dftracer-optimization-kb]] [[software-mpi]]

Communication-component sibling of `dftracer-io-optimization`. The metric key used by the MCP
optimization tools is `comm_wait` (see `_L1_STRATEGIES`/`_L2_STRATEGIES`/`_L3_STRATEGIES["comm_wait"]`
in `mcp_tools/tools/optimizations/strategies.py`); related classification keys: `comm`,
`mpi_wait`, `collective`, `sync_time`, `allreduce`.

## MANDATORY: Exhaustive Dimension Checklist (walk ALL, every session)

Every row in the resulting `opt_proposal_table` MUST carry `app_impact_pct` (% of current application wall time, measured or bounded-estimate) and `system_impact_pct` (throughput/bandwidth/utilization effect, 0 if not measured/applicable) — columns: `#`, `Strategy`, `Description of Optimization`, `App Impact`, `System Impact`, `Weighted Score` (50/50 weighted, auto-sorted descending by the tool). Never omit these fields.

1. **L1 overlap communication with compute** — non-blocking collectives (`MPI_Iallreduce`,
   `MPI_Ibcast`) launched early, gradient-ready hooks that start all-reduce before the full
   backward pass finishes.
2. **L1 message aggregation** — batch multiple small messages into one larger send/recv
   without changing the logical communication topology (same category as I/O's small-request
   coalescing — coalesce, don't restructure).
3. **L2 collective algorithm selection** — topology-aware algorithm choice
   (`OMPI_MCA_coll_hcoll_enable`, MPICH's algorithm-selection env vars) picks tree vs. ring vs.
   recursive-doubling based on message size/rank count, without changing WHAT is communicated.
4. **L2 eager/rendezvous protocol tuning** — eager-limit / rendezvous threshold tuning so
   small messages use the low-latency path and large messages use the bandwidth-optimized path.
5. **L2 transport/provider selection** — libfabric/UCX provider selection (verbs vs. ofi vs.
   tcp), RDMA vs. TCP fallback — check what's actually available on this system's fabric
   first (see the system skill).
6. **L3 topology-aware rank placement** — bind ranks so frequently-communicating pairs are
   co-located (same node/switch) to minimize hop count — `--map-by`, rankfiles.
7. **L3 network/interconnect tuning** — NIC binding, queue-pair/RDMA depth, congestion-control
   parameters — check tunability (often admin-only) before proposing.
8. **L3 collective-buffer sizing at the system level** — `cb_nodes`/`CRAY_CB_NODES_MULTIPLIER`
   (Cray MPICH ignores `cb_nodes` directly — see `software-mpi` skill) for MPI-IO collective
   I/O that is fundamentally a communication-shaped operation.
9. **Compute/communication overlap** — shared dimension with the compute skill; record once,
   cross-reference from whichever skill's checklist is walked second.

Per category: run the literature search before marking "not applicable." Never silently omit
a category.

## MANDATORY: never change the app's actual communication pattern as an "optimization"

Do not propose restructuring WHICH ranks talk to which, changing a collective's semantics
(e.g. replacing an `MPI_Allreduce` with a functionally different reduction), or dropping
synchronization points that the app's correctness depends on, just because an alternate
communication shape measured faster elsewhere. Keep the communication pattern's logical shape
fixed and speed up how the stack executes it: algorithm selection, protocol tuning, topology
placement, overlap with compute — never a semantic rewrite of the collective operation itself.

## L1 Application Strategies (metric: comm_wait)

- **Overlap gradient all-reduce with backward-pass compute** — register gradient-ready hooks
  to launch all-reduce as soon as each layer's gradient is computed. (Thakur, R.,
  Rabenseifner, R., Gropp, W., *Optimization of Collective Communication Operations in
  MPICH*, IJHPCA 19(1), 2005, https://doi.org/10.1177/1094342005051521)

## L2 Software/Middleware Strategies (metric: comm_wait)

- **Topology-aware collective algorithm selection** — `OMPI_MCA_coll_hcoll_enable=1` (OpenMPI)
  or the MPICH equivalent, selecting tree/ring/recursive-doubling by message size and topology.

## L3 System/Network Strategies (metric: comm_wait)

- **Topology-aware rank binding** — co-locate communicating ranks to minimize inter-node hop
  count (rankfile, `--map-by numa`).
- **Cray MPICH `CRAY_CB_NODES_MULTIPLIER`** — Cray MPICH ignores the standard `cb_nodes` hint;
  only this env var raises the aggregator count for MPI-IO collective operations (see
  `software-mpi` skill).

## Built-in Citations

- Thakur, R., Rabenseifner, R., Gropp, W., *Optimization of Collective Communication
  Operations in MPICH*, IJHPCA 19(1), pp. 49-66, 2005,
  https://doi.org/10.1177/1094342005051521

## Metric to Optimization Goal Mapping

| Metric | Optimization goal |
|---|---|
| `comm_wait` / `mpi_wait` / `collective` / `sync_time` / `allreduce` | Reduce time ranks spend blocked on communication, without changing what/who they communicate with |

## Ordering Rule

Communication is optimized SECOND in the canonical I/O -> communication -> memory -> compute
order — after I/O (usually the largest lever) but before memory/compute tuning, since
communication-bound stalls often mask memory- or compute-bound behavior underneath.

## When the bottleneck lives in library code, not the deck/config

Confirmed on vpic-kokkos (2026-07-14): the diagnosed `MPI_Allreduce` cost was inside
`libvpic.a` itself (three small per-timestep reductions coalescible into one — see
`workload-vpic-kokkos` skill), not in the input deck or a runtime flag. Applying and
measuring that fix requires an incremental library relink, not just a deck recompile —
assess that rebuild cost against the remaining time on a shared/short allocation before
committing to it mid-session. It is reasonable to defer the actual measured trial to a
dedicated validation step (with its own allocation and replicate budget) rather than risk
a half-finished rebuild or a dirty annotated source tree in a tight window — report the
patch and citation as a high-confidence candidate, explicitly unmeasured, rather than
fabricating a result.

## A "zero communication events" reading can be a threshold artifact, not a real absence

Confirmed on PECAN/PDBspheres (2026-07-20): the analyzer's Layer Breakdown showed zero events
for `communication-except-io` (`cpu-gpu-transfer`, wrapped via `dft_event_logging` in
`pecan/trainer.py`'s DDP main process). Direct grep of the compact `.pfw.gz` files found it
WAS firing — 3264 events, 25.58s aggregate across 16 ranks x 2 epochs — just at ~15,000:1
fewer events than the HDF5 open-storm it was statistically buried next to (50.5M `cat=hdf5`
events in the same trace). Same failure family as `bug-diagnoser-zero-observations-checkpoint`
and the compute-layer-bucketing gap in `dftracer-compute-optimization`. **Before reporting a
category as having zero/no cost, grep the raw compact traces for its literal `cat` string and
sum `dur` directly** — a low-frequency category can be real and present while still rounding
to invisible next to a million-event dominant one.

## GPU collective (RCCL/NCCL) allreduce time is invisible when dftracer is built with HIP
tracing OFF

Confirmed same session: PyTorch DDP fuses gradient-allreduce into `loss.backward()` via
bucket-ready autograd hooks, so with `DFTRACER_ENABLE_HIP_TRACING=OFF` (the default per
`feedback-dftracer-install-rocm-mpi` unless the app directly calls HIP/ROCm), the allreduce
cost is silently folded into whatever `dft_event_logging` context wraps `loss.backward()`
(here, `"compute"`/`"model-backward"`) — it cannot be isolated from actual backward-pass
compute without HIP-level or rocprof/RCCL-level tracing. If communication needs to be
precisely quantified for a DDP/multi-GPU workload, this is a real limitation of function-mode
Python tracing alone — flag it explicitly as an open coverage gap rather than assuming zero
comm cost or attributing all of `model-backward`'s time to compute.

## PECAN-specific finding (2026-07-20): communication is NOT the bottleneck for this app

For a 16-rank (4N x 4GPU) EGNN/PyG DDP training run, `cpu-gpu-transfer` cost only 25.58s
aggregate (~1.6s/rank, ~2.6% of the 961s I/O bottleneck, <1% of wall time) — allreduce
overlap with backward is already the DDP default, `pin_memory=True` was already set, and
message-coalescing the per-sample `.to(device)` calls has a real but tiny (<1%) ceiling.
Do not spend optimization budget on communication for small-graph GNN workloads like this
one where I/O (HDF5 metadata storm) dominates by 2 orders of magnitude — always check the
diagnosed severity/prevalence ranking before investing in a lower-ranked dimension.

**Scheduler-owned MPI is a structural trace blind spot -- use the scheduler's own summary as
ground truth (1000genome-workflow / Pegasus PMC, 2026-07-26):** when the workflow scheduler
itself (here, `pegasus-mpi-cluster`, a C++ MPI binary) is not dftracer-annotated, its
master-worker dispatch traffic produces **zero MPI events in any trace**, even though real
MPI communication is happening (task dispatch/result messages). An empty scan for `MPI_*`
spans in this situation is a coverage gap, not evidence of zero communication -- **never
report "zero communication" from an unannotated component's absence in the trace.** Instead,
use the scheduler's own instrumentation/summary line as ground truth: PMC prints a
`[cluster-summary ... bytes_sent=<N> ...]` line giving the actual byte count (29,956 bytes
total across a 316.5s/32-rank run here -- a provably negligible ceiling, 0.0002% of run time,
but PROVEN via the scheduler's own counter, not assumed from empty trace evidence). State
the coverage gap explicitly in any report that draws a "communication negligible" conclusion
from a scheduler-owned MPI binary.

**DAG-max-width pre-check before proposing any scheduling/rank-count lever:** before
proposing a worker-pool/rank-count change for a DAG-scheduled workflow, compute the DAG's
level-by-level width (parse the `.dag` file's TASK/EDGE structure) and compare it to the
current worker count. If max width < worker count, the pool is never the binding constraint
and any "low utilization %" metric is a DAG-shape artifact (parallelism ceiling set by the
DAG's own structure, e.g. narrow serialization barriers between wide fan-out levels), not a
schedulable inefficiency -- do not "fix" it by changing rank count. Confirmed on this
workflow: 8-level DAG, max width 17 (individuals fan-out), vs. 31 workers -- the 19.4%
utilization figure is expected and correct, not a target for optimization.
