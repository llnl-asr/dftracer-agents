---
name: dftracer-memory-optimization
description: Memory-component bottleneck-to-optimization mappings, papers, and L1/L2/L3 strategies for the dftracer optimization pipeline
---

Cross-references: [[dftracer-io-optimization]] [[dftracer-compute-optimization]] [[dftracer-communication-optimization]] [[dftracer-optimization-kb]]

Memory-component sibling of `dftracer-io-optimization`. The metric key used by the MCP
optimization tools is `mem_bw` (see `_L1_STRATEGIES`/`_L2_STRATEGIES`/`_L3_STRATEGIES["mem_bw"]`
in `mcp_tools/tools/optimizations/strategies.py`); related classification keys: `memory`,
`cache_miss`, `numa`.

## MANDATORY: Exhaustive Dimension Checklist (walk ALL, every session)

1. **L1 buffer reuse/pooling** — reuse allocated buffers across iterations instead of
   alloc/free churn; object/tensor pooling.
2. **L1 in-place operations** — avoid unnecessary intermediate copies (in-place tensor ops,
   avoiding redundant `malloc`+`memcpy` where the app's logic permits it losslessly).
3. **L1 cache blocking / tiling** — restructure the hot loop's memory access order to
   maximize reuse of data already resident in cache (raises arithmetic intensity — shared
   dimension with the compute skill's vectorization work).
4. **L2 allocator selection** — swap the default allocator for a NUMA-aware one
   (jemalloc/tcmalloc) via `LD_PRELOAD`, tuned arena count.
5. **L2 pinned memory for host-device transfers** — `cudaMallocHost`/`hipHostMalloc`-backed
   pinned buffers so DMA transfers don't need an extra staging copy — only pays off when
   combined with correct core/GPU-die affinity (see the compute skill; measure both together
   as separate line items, never bundle their attribution).
6. **L2 huge pages / TLB pressure reduction** — transparent huge pages or explicit
   `madvise(MADV_HUGEPAGE)` for large allocations to cut TLB miss rate.
7. **L2 HDF5/MPI-IO internal buffer sizing** — chunk cache size, collective I/O buffer size —
   shared boundary with the I/O skill; record the finding once in whichever skill owns the
   specific tunable (HDF5 chunk cache -> io skill; general allocator arena -> here).
8. **L3 NUMA memory binding** — `numactl --cpunodebind=<n> --membind=<n>` or `hwloc-bind`, so
   a process's memory lives on the same NUMA node as its compute (shared dimension with the
   compute skill — record once, cross-reference).
9. **L3 page-cache / VM tuning** — `vm.dirty_ratio`, `vm.vfs_cache_pressure`, huge-page
   kernel settings — check tunability (often admin-only) before proposing.
10. **L3 memory-bandwidth roofline check** — establish whether the workload is actually
    memory-bandwidth-bound (roofline analysis) before proposing any memory-layer tuning; a
    compute-bound kernel gains nothing from memory tuning.

Per category: run the literature search before marking "not applicable." Never silently omit
a category.

## MANDATORY: never change the app's actual memory footprint semantics as an "optimization"

Do not propose reducing batch size, dropping cached data the app's correctness depends on, or
truncating precision as a memory "optimization" — that changes what the app computes/holds,
not how efficiently the system serves the SAME memory access pattern. Buffer pooling, in-place
ops, and cache blocking are safe ONLY when the app's logical data lifetime and values are
unchanged; verify with a correctness check (byte-identical output) before crediting a result.

## L1 Application Strategies (metric: mem_bw)

- **Increase batch/tile size to raise arithmetic intensity** — restructure the hot loop to
  reuse loaded data across more operations before eviction (cache blocking). (McCalpin, J.D.,
  *Memory Bandwidth and Machine Balance in Current High Performance Computers*, IEEE TCCA
  Newsletter, 1995, https://www.cs.virginia.edu/stream/ref.html)

## L2 Software/Middleware Strategies (metric: mem_bw)

- **Transparent huge pages / `madvise`** — back large allocations with huge pages to cut TLB
  miss rate (`MALLOC_MMAP_THRESHOLD_`, `MALLOC_TRIM_THRESHOLD_`).
- **NUMA-aware allocator** — `LD_PRELOAD=libjemalloc.so` with `MALLOC_CONF=narenas:<numa_nodes>`
  to keep per-thread arenas NUMA-local.

## L3 OS/Hardware Strategies (metric: mem_bw)

- **NUMA binding** — `numactl --cpunodebind=<n> --membind=<n>` (see `strategies.py` L3
  `mem_bw` entry).
- **Page-cache tuning** — `vm.vfs_cache_pressure`, `vm.dirty_ratio` — admin-only on most HPC
  systems, verify tunability first.

## Built-in Citations

- McCalpin, J.D., *Memory Bandwidth and Machine Balance in Current High Performance
  Computers*, IEEE Computer Society TCCA Newsletter, 1995,
  https://www.cs.virginia.edu/stream/ref.html
- Williams, S., Waterman, A., Patterson, D., *Roofline: An Insightful Visual Performance
  Model*, CACM 52(4), 2009, https://doi.org/10.1145/1498765.1498785 (bandwidth- vs.
  compute-bound classification)

## Metric to Optimization Goal Mapping

| Metric | Optimization goal |
|---|---|
| `mem_bw` / `memory` / `cache_miss` / `numa` | Reduce memory-bound stall time / increase effective bandwidth utilization without changing what data the app holds or when |

## Ordering Rule

Memory is optimized THIRD in the canonical I/O -> communication -> memory -> compute order —
after I/O and communication, since memory-bound stalls are often masked by (or masking) those
larger-magnitude bottlenecks, but before compute tuning (a compute-bound kernel gains nothing
from memory tuning; verify with roofline first).
