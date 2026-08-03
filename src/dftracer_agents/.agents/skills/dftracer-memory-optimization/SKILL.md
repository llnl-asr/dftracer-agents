---
name: dftracer-memory-optimization
description: Memory-component bottleneck-to-optimization mappings, papers, and L1/L2/L3 strategies for the dftracer optimization pipeline
---

Cross-references: [[dftracer-io-optimization]] [[dftracer-compute-optimization]] [[dftracer-communication-optimization]] [[dftracer-optimization-kb]] [[software-rocm]]

**MI300A APU note:** see `software-rocm`'s "unified memory is INTRA-PACKAGE
only" and "`XLA_PYTHON_CLIENT_PREALLOCATE=false`" sections before assuming
unified-memory systems make allocator/pool-reservation levers inert —
pinned-memory/NUMA levers are inert there, but framework preallocation levers
can matter MORE, not less, because the reserved pool competes with the CPU
side for the same physical HBM.

Memory-component sibling of `dftracer-io-optimization`. The metric key used by the MCP
optimization tools is `mem_bw` (see `_L1_STRATEGIES`/`_L2_STRATEGIES`/`_L3_STRATEGIES["mem_bw"]`
in `mcp_tools/tools/optimizations/strategies.py`); related classification keys: `memory`,
`cache_miss`, `numa`.

## MANDATORY: Exhaustive Dimension Checklist (walk ALL, every session)

Every row in the resulting `opt_proposal_table` MUST carry `app_impact_pct` (% of current application wall time, measured or bounded-estimate) and `system_impact_pct` (throughput/bandwidth/utilization effect, 0 if not measured/applicable) — columns: `#`, `Strategy`, `Description of Optimization`, `App Impact`, `System Impact`, `Weighted Score` (50/50 weighted, auto-sorted descending by the tool). Never omit these fields.

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

**Cheap first check before the literature search**: dismiss the capacity-related categories
(1-2, 6-7, 9, and gradient checkpointing) from `dftracer_service` node-counter evidence rather
than assumption. The node-counter trace emits a 1Hz `"name":"memory"` counter series with the
full `/proc/meminfo` field set:
`zcat <WS>/traces/service_*.pfw.gz | grep '"name":"memory"' | grep -o '"MemAvailable":[0-9]*'`
gives min/max peak node memory use directly; also check `SwapTotal`, `Dirty`, `Writeback`,
`HugePages_Total` in the same series. If peak use is a small fraction of total and swap/dirty/
writeback are all 0, most of the checklist's capacity categories are dismissible in one command
— do this before spending time on a literature search for categories that evidence already
rules out. (Confirmed on ray_molformer/MI300A: 22.5 GiB peak of ~490 GiB = 4.6%, zero swap,
zero spill — memory was not the bottleneck and gradient checkpointing would have been a pure
regression on an already compute-bound job.)

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

## Unified-memory APUs (MI300A) make several classic memory levers structurally inert

Confirmed on PECAN/PDBspheres (2026-07-20, Tuolumne/MI300A): CPU and GPU share one HBM3 pool
on this architecture, which removes the host<->device staging-copy pressure that dominates
discrete-GPU memory tuning. `pin_memory=True` and other staging-copy optimizations are
already default/inert here — do not propose them as a lever on MI300A-class unified-memory
systems; cite the architecture reasoning (arXiv 2508.12743) and flag N/A early rather than
walking the full measurement path. Huge pages / `madvise(MADV_HUGEPAGE)` are also a
guaranteed no-op on this system's glibc (not page-aligned malloc) — confirmed via KB, not
re-tested. A workload with no measured HBM-bandwidth pressure (data-loading-bound instead,
per the diagnosed trace profile) should get a documented "not memory-bound" verdict rather
than forcing memory-layer changes that have no headroom to improve.

**`gc.disable()` is the wrong lever for acyclic object graphs -- measured regression
(1000genome-workflow `individuals.py`, 2026-07-26):** `gc.disable()+gc.freeze()` was tried as
a memory/GC-pressure optimization for a Python VCF-parsing loop generating thousands of
short-lived per-individual buffer objects. Measured **+1.7% REGRESSION**, not a win -- do NOT
apply. Root cause: the generational cyclic collector's cost is proportional to reference-cycle
scanning, and this workload's per-individual objects (strings, lists, dicts with no
back-references) are acyclic and already collected cheaply via refcounting; disabling the
cyclic GC just defers memory reclamation (larger working set, more page faults) without
removing any real collection cost. Before proposing `gc.disable()` as a fix, confirm the
object graph actually contains reference cycles (e.g. via `gc.get_stats()` collection counts
or object graph inspection) -- for interpreter-heavy workloads with no cycles, this lever has
negative expected value.

## Verify a checkpoint file actually exists before proposing shared/mmap weight loading

Confirmed on ray_molformer (2026-08-02, 4-node/16-GPU): "shared/mmap model weights across N
worker actors" looks like an obvious lever whenever N workers each construct the same model,
but it has no target if the model is built from config rather than loaded from a checkpoint
file. Before proposing it, grep the trace for any weight-file read:
`zcat *.pfw.gz | grep -oE '"[^"]*\.(bin|safetensors|pt|pth|ckpt)"' | sort | uniq -c`. On
ray_molformer this returned zero matches across 27.3M events -- the model is constructed
in-process from a HF `PretrainedConfig`, so there is no file to mmap, dedupe, or pass
zero-copy through Ray's object store. Confirming absence first avoids proposing a lever with
no possible mechanism.
