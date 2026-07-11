---
name: dftracer-compute-optimization
description: Compute-component bottleneck-to-optimization mappings, papers, and L1/L2/L3 strategies for the dftracer optimization pipeline
---

Cross-references: [[dftracer-io-optimization]] [[dftracer-communication-optimization]] [[dftracer-memory-optimization]] [[dftracer-optimization-kb]]

This skill is the compute-component sibling of `dftracer-io-optimization`. Same rules apply
(citation-backed, never "do less", never change the app's actual computation/algorithm as a
"pattern swap" unless correctness is verified byte-for-byte) — this file adds the
compute-specific catalog: the metric key used by the MCP optimization tools is `compute_time`
(see `_L1_STRATEGIES`/`_L2_STRATEGIES`/`_L3_STRATEGIES["compute_time"]` in
`mcp_tools/tools/optimizations/strategies.py`).

## MANDATORY: Exhaustive Dimension Checklist (walk ALL, every session)

Never stop at the first compute fix that helps. Every compute-optimization pass walks this
full checklist and records a verdict for EACH category (Applied & measured / Applicable, not
measured / Not applicable — with reason), exactly like the I/O checklist:

1. **L1 algorithmic/vectorization** — SIMD/vectorized loops, batched tensor ops, blocking/tiling
   for cache reuse, mixed precision (fp16/bf16/tf32) where numerically safe.
2. **L1 kernel/library selection** — swap a naive implementation for a vendor-tuned kernel
   (BLAS/LAPACK, MIOpen/rocBLAS, cuBLAS/cuDNN) without changing the algorithm's output.
3. **L1 compute/I-O or compute/comm overlap** — async execution so compute doesn't idle-wait
   on data movement (see also the communication and memory skills for the other side of the
   overlap).
4. **L2 compiler/runtime flags** — optimization level (`-O3`), LTO, target-specific ISA flags
   (`-march=native`/`-mcpu=`), math-library thread count (`OMP_NUM_THREADS`, `MKL_NUM_THREADS`,
   `ROCBLAS_LAYER`) matched to physical core/CU count (never oversubscribed).
5. **L2 threading model** — OpenMP scheduling policy (`static`/`dynamic`/`guided`), thread
   pinning/affinity, GPU stream concurrency.
6. **L2 auto-tuning** — vendor kernel auto-tuners (MIOpen find-mode, cuDNN benchmark mode)
   that pick the fastest kernel variant for the actual problem shape without altering results.
7. **L3 CPU/GPU frequency & power** — performance governor, GPU clock/perf-level profile —
   check whether this is even user-tunable (often admin-only) before proposing it.
8. **L3 NUMA/affinity binding** — pin compute processes to the NUMA node/CU set holding their
   working set (shared with the memory skill's NUMA dimension — record once, cross-reference).
9. **L3 hardware capability check** — confirm the target actually has the vector/tensor unit a
   proposed technique assumes (AVX-512, matrix cores, etc.) before proposing it.
10. **Compute/communication overlap** — overlapping collective communication with backward-pass
    or independent compute (shared dimension with the communication skill).

Per category: run the literature search (arXiv/Semantic Scholar/`rag_search`/`opt_kb_lookup`)
before marking "not applicable" for lack of a technique. Never silently omit a category.

## MANDATORY: never change the app's actual computation as an "optimization"

Do not propose swapping an algorithm for a numerically-different one, reducing solver
precision/tolerance, skipping compute steps, or reducing epoch/iteration counts just because
the alternative measured faster. That is *doing less* or *changing correctness*, not
optimizing the system's ability to run the SAME computation faster. Kernel/library swaps and
vectorization are safe ONLY when output is verified byte-identical (or within the algorithm's
own documented numerical tolerance) before/after.

## L1 Application Strategies (metric: compute_time)

- **Vectorize/batch the hot loop** — replace per-element scalar loops with a vectorized
  (NumPy/SIMD) batched operation. (Williams et al., Roofline, CACM 2009,
  https://doi.org/10.1145/1498765.1498785)
- **Mixed precision** — use fp16/bf16/tf32 where the algorithm's numerical tolerance allows,
  verified against a full-precision reference run.
- **Kernel/library swap** — replace a naive implementation with a vendor-tuned kernel
  (rocBLAS/MIOpen on AMD APUs, cuBLAS/cuDNN on NVIDIA), output-checked for equivalence.

## L2 Software/Middleware Strategies (metric: compute_time)

- **Thread-count matching** — `OMP_NUM_THREADS`/`MKL_NUM_THREADS` set to the physical
  core/CU count, not oversubscribed (see `strategies.py` L2 `compute_time` entry).
- **Auto-tuning mode** — enable vendor kernel-search/auto-tune mode for the actual problem
  shape (MIOpen find-mode, cuDNN benchmark=True).

## L3 OS/Hardware Strategies (metric: compute_time)

- **Frequency governor / clock profile** — `cpupower frequency-set -g performance`,
  `rocm-smi --setperflevel high` (see `strategies.py` L3 `compute_time` entry). Sudo-gated;
  check tunability before proposing.
- **NUMA/affinity binding** — `numactl --cpunodebind=<n>`/`hwloc-bind`, shared dimension with
  the memory skill.

## Built-in Citations

- Williams, S., Waterman, A., Patterson, D., *Roofline: An Insightful Visual Performance
  Model for Multicore Architectures*, CACM 52(4), 2009, https://doi.org/10.1145/1498765.1498785
- Devarajan, H. et al., *DLIO: A Data-Centric Benchmark for Scientific Deep Learning
  Applications*, CCGrid 2021, https://ieeexplore.ieee.org/document/9499416 (compute/data
  overlap for DL training loops)

## Metric to Optimization Goal Mapping

| Metric | Optimization goal |
|---|---|
| `compute_time` | Reduce wall-clock time spent in compute kernels without changing output |
| `cpu_bound` / `gpu_util` / `flops` | Classify as compute-category for canonical ordering (I/O -> communication -> memory -> compute) |

## Ordering Rule

Compute is optimized LAST in the canonical I/O -> communication -> memory -> compute order
(see `_category_sort_key` in `strategies.py`) — a compute-bound kernel tuned before the I/O or
communication bottleneck is fixed is optimizing the wrong stage of the pipeline first.
