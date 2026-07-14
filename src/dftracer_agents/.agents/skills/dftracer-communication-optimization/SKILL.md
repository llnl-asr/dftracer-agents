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
