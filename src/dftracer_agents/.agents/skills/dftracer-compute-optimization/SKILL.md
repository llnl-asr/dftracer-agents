---
name: dftracer-compute-optimization
description: Compute-component bottleneck-to-optimization mappings, papers, and L1/L2/L3 strategies for the dftracer optimization pipeline
---

Cross-references: [[dftracer-io-optimization]] [[dftracer-communication-optimization]] [[dftracer-memory-optimization]] [[dftracer-optimization-kb]] [[software-rocm]]

This skill is the compute-component sibling of `dftracer-io-optimization`. Same rules apply
(citation-backed, never "do less", never change the app's actual computation/algorithm as a
"pattern swap" unless correctness is verified byte-for-byte) — this file adds the
compute-specific catalog: the metric key used by the MCP optimization tools is `compute_time`
(see `_L1_STRATEGIES`/`_L2_STRATEGIES`/`_L3_STRATEGIES["compute_time"]` in
`mcp_tools/tools/optimizations/strategies.py`).

## MANDATORY: Exhaustive Dimension Checklist (walk ALL, every session)

Every row in the resulting `opt_proposal_table` MUST carry `app_impact_pct` (% of current application wall time, measured or bounded-estimate) and `system_impact_pct` (throughput/bandwidth/utilization effect, 0 if not measured/applicable) — columns: `#`, `Strategy`, `Description of Optimization`, `App Impact`, `System Impact`, `Weighted Score` (50/50 weighted, auto-sorted descending by the tool). Never omit these fields.

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
  Confirmed high-value on vpic-kokkos (2026-07-14, Tuolumne): a Kokkos-OpenMP build's
  run script never set `OMP_NUM_THREADS`/`OMP_PROC_BIND`/`OMP_PLACES`, silently running
  1 thread/rank with 6 idle cores/rank (96 cores/node ÷ 16 ranks/node). Adding
  `-c<cores_per_task>` to the launcher (`flux run -c6 ...`) plus
  `OMP_NUM_THREADS=6 OMP_PROC_BIND=spread OMP_PLACES=cores` — a pure runtime env
  change, no rebuild — cut job wall time 41% (210.2s → 124.0s) at 128 ranks/8 nodes.
  Don't trust an app's own internal thread-count print to confirm this: VPIC prints
  a legacy `n_pipeline` counter unrelated to the Kokkos execution space and stayed at
  `1` even as wall time dropped — verify via measured wall-clock time, not an
  app-reported thread count. See `workload-vpic-kokkos`.
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

## Analyzer preset "Layer Breakdown" can hide a fully-traced compute cost

Confirmed on PECAN/PDBspheres (2026-07-20): `pecan/trainer.py` correctly wraps
`model-forward`/`model-backward` in `dft_event_logging("compute", ...)`, and the events ARE
in the raw trace (6528 `cat=compute` events, 776s aggregate across 16 ranks x 2 epochs — the
single LARGEST per-rank category, bigger than the entire preprocess bucket). But the
`posix`/`dlio`/`generic` dfanalyzer presets have **no `compute` layer bucket at all**, so the
Layer Breakdown table shows zero for it regardless — this looks exactly like "compute isn't
traced" but is actually a preset-bucketing gap, not a coverage gap. **Before concluding GPU
compute is untraced or free, `zcat`/grep the compact `.pfw.gz` files directly for
`"cat":"compute"` event counts and sum `dur`** rather than trusting the Layer Breakdown alone.

## PECAN-specific findings (2026-07-20 session, MI300A/gfx942, EGNN model)

GPU fwd+bwd (776s aggregate, mean 138ms/backward + 99ms/forward) was the single largest
per-rank cost once measured directly (bigger than the 412s preprocess bucket, which runs
overlapped in DataLoader workers). `radius_graph`/`knn_graph` swaps are NOT APPLICABLE — they
change the actual edge set, which changes model computation/correctness (forbidden "pattern
swap"). NUMA/core-affinity is a confirmed no-op on this exact MI300A system for a third
workload class (see `system-tuolumne`) — do not re-propose it without a fundamentally different
mechanism.

**REVISED 2026-07-21 (baseline5 pass, after reading the actual model code, not just
profiling):** the initial 2026-07-20 ranking above put AMP bf16 first (~12%) — this was an
UNGROUNDED ESTIMATE made before reading `model/egnn.py`. The real code shows `hidden_nf=20`,
so every `nn.Linear` GEMM in the 4-layer EGNN is ≤20-wide — LAUNCH/MEMORY-BOUND, not
FLOP-bound. bf16 cuts FLOPs, not kernel-launch count, so its real ceiling here is ~2%, not
12%. **General lesson: always read the model's actual hidden-dimension/layer-width before
ranking AMP vs kernel-fusion for a GNN or small-MLP model** — for tiny-hidden models,
`torch.compile`/kernel fusion (fewer launches) beats mixed precision (fewer FLOPs per launch).
Revised ranking for this app: `torch.compile(dynamic=True)` fusion (~6-8%, risk: PyG's
data-dependent edge count from the distance-cutoff filter can trigger recompiles/graph-breaks
— measure graph-break count before crediting) > removing the per-step host-device sync
serializer in the loss block (~3-4%, but see below — more invasive than "drop `.cpu()`") >
AMP bf16 (revised ~2%, demoted) > precompute/cache invariant graph adjacency (~1-3%) > MIOpen
autotune — CONFIRMED NOT APPLICABLE, EGNN has no convolution kernels, only
Linear+scatter_add+BatchNorm1d+activation, nothing for a conv auto-tuner to tune >
`pairwise_distances` → `torch.cdist` swap — CONFIRMED NOT APPLICABLE (not just low-ceiling):
the app computes per-EDGE distances (`PairwiseDistance`, O(E), already gathered by
`edge_index`), while `cdist` computes a full O(N²) pairwise matrix — swapping would change the
actual computed values, a forbidden correctness-breaking pattern-swap, not a valid kernel
substitution.

**General lesson on the host-device sync serializer:** scan training loops for per-step
`.cpu()`/`.item()` calls on GPU tensors — a blocking device sync per step is a common,
easily-missed serializer. BUT check how pervasive it actually is before calling it "low risk":
in PECAN's `trainer.py` the pattern is NOT a single isolated `.cpu()` call — the loss block has
up to 7+ sync points (main loss + up to 5 bond-losses + a debug branch + the logged
`loss_val`), and some "device" tensors were never actually on-device to begin with:
`torch.FloatTensor([x.to(self.device) for x in batch])` materializes a NEW CPU tensor from a
list of GPU scalars, silently discarding the `.to(self.device)` work inside the list
comprehension. A fix here is a full loss-block rewrite (keep everything device-resident, one
`.item()` only for the logged value), not a 2-line diff — treat "remove blocking .cpu() sync"
proposals as needing a full call-site audit, not a one-off point fix, before estimating risk as
"low".

**MEASURED 2026-07-22 (baseline5 scale, 4N×4GPU DDP, live validation run):**
`torch.compile(model, dynamic=True)` was applied to the EGNN model (gated by env var
`PECAN_TORCH_COMPILE=1`, wrapped before `DDP(...)` in `model/model_trainer.py`) and run for a
single replicate. Epoch-1 (compile+warmup): 82.05s → 143.97s (**+75.5%, one-time cost**).
Epoch-2 (steady state): 31.54s → 5.77s (**-81.7%**). Graph-break count: 6 total events / 5
distinct compile frames, ALL confined to warmup (from PyG's `scatter` computing
`dim_size=int(index.max())` — a `Tensor.item()` graph break — and `aten.nonzero.default`, both
downstream of the distance-cutoff filter, not the filter itself) — **zero recompiles** across
the remaining ~200 steps, so `dynamic=True` successfully avoided the anticipated
per-step-recompile pathology. Loss trajectories look qualitatively consistent (opt1 epoch-1/2
avg loss 0.89/0.58 vs baseline 1.01/0.59) but were not validated via a controlled same-batch/
same-seed comparison.

**CAVEAT — treat the -81.7% number as directional, not final:** (a) single replicate only, the
usual ≥5-replicate rule was not met (pdebug time-boxed); (b) the *baseline itself* improves
82.05s→31.54s (-61.5%) from epoch 1→2 purely from OS/page-cache warmup on the HDF5 shards, so
part of the apparent compile-only win is confounded with that same warmup effect — the
compile-attributable fraction is smaller than the raw -81.7% suggests. Before crediting this
as a final PECAN compute optimization, run ≥5 replicates and isolate the warmup confound (e.g.
compare epoch-2-vs-epoch-2, or use a longer run where both curves have converged).

**FOLLOW-UP 2026-07-22 (3-epoch de-confound validation, same allocation, opt2_compile3ep):**
Extended the compiled run to 3 epochs to check whether the epoch-2 steady-state number was
itself still cooling from the epoch-1 warmup transient. Result: epoch-2 = 6.03s, epoch-3 =
6.36s — essentially flat (+5.5%, within single-run noise). Graph-break count stayed at 5
distinct compile frames with zero recompiles through epoch 3, confirming `dynamic=True`
remains stable well past the warmup window. This is a real, positive signal that the
compiled side's ~6s number is genuine steady state, not still-warming — but it is NOT a full
de-confound of the -81.7% headline: no 3-epoch BASELINE (`PECAN_TORCH_COMPILE=0`) run was
completed in this time-boxed pdebug window, so there is still no direct epoch-3-vs-epoch-3
comparison, and both sides remain single-replicate. **Before citing -81.7% (or any specific
percentage) as a final number, run (a) a 3-epoch baseline for a true epoch-3-vs-epoch-3
comparison, and (b) ≥5 replicates each side.**

**RESOLVED 2026-07-22 (3-epoch baseline de-confound, `opt3_baseline3ep` vs `opt2_compile3ep`,
same 4N×16-rank DDP scale) — REVERSES the -81.7% headline.** The missing matched 3-epoch
UNCOMPILED baseline was run: epoch1=11.36s, epoch2=5.85s, epoch3=5.77s — essentially identical
to (marginally FASTER than) the compiled run's epoch2=6.03s/epoch3=6.36s. **The earlier -81.7%
number was entirely the epoch1→epoch2 HDF5 page-cache warmup confound present in baseline5's
own 2-epoch numbers, not a real `torch.compile` effect.** `torch.compile(dynamic=True)` on this
EGNN model (`hidden_nf=20`) provides NO measurable steady-state speedup, and its one-time
compile+warmup cost is a pure net loss: +1200% on epoch 1 (147.9s compiled vs 11.36s
uncompiled) with no offsetting benefit anywhere. **Do not recommend `torch.compile` for PECAN
based on current evidence** — this reverses the earlier "revised ranking" that promoted it
above AMP bf16. Both sides are still single-replicate; if revisited, run ≥5 replicates each
before trusting a delta either way. General lesson: a launch-bound-model hypothesis
(`hidden_nf=20` → fusion should help) is not a substitute for a matched, de-confounded
measurement — the code-reading-based reasoning that promoted `torch.compile` in the first
place was directionally sensible but the actual measured effect turned out to be zero once the
warmup confound was removed.

The loss-block host-device sync rewrite (proposal above) was correctly SKIPPED under time
pressure rather than rushed — still open for a future session with a dedicated time budget.

**DONE 2026-07-22 (loss-block host-device sync rewrite, baseline5 scale, 4N×16-rank DDP,
single replicate):** Applied the previously-deferred fix in `pecan/trainer.py`'s
`train_one_epoch`/`eval_one_epoch`: (1) replaced `torch.FloatTensor([x.to(device) for x in
batch])` with `torch.stack([x.to(device).view(()) for x in batch])` for
`output_aff`/`output_rmsd`/`output_score`/bond-loss targets — the `.view(())` is required, not
cosmetic: `torch.FloatTensor` silently flattens `(1,)`-shaped per-sample tensors to 0-dim
scalars during construction, while plain `torch.stack` preserves `(1,)`, producing a `(B,1)`
tensor instead of `(B,)` and crashing `BCELoss` with a target/input size mismatch — caught by
the live validation run (first attempt crashed), not the standalone correctness check alone.
(2) Removed the `.cpu().float()` calls in the loss block's 7 loss terms now that both operands
are correctly GPU-resident; kept exactly one `.item()` for the logged `loss_val`. **Result:
epoch-2 (steady-state) time dropped from `io_opt4_finalize`'s 29.72s to 28.69s (-3.5%) and from
`baseline5`'s 31.54s (-9.0%)**, single replicate only — directionally consistent with the
~3-4% ceiling estimate above. Correctness verified via standalone old-vs-new script on
fixed-seed synthetic batches (bit-identical loss value: 1.0525270700454712 both sides, all 6
tensor fields shape+value matched) and via a full 2-epoch DDP run completing cleanly with the
dftracer worker-finalize trace-completeness fix still intact (0 empty trace files, 48/48).
Before citing -3.5%/-9.0% as final, run ≥5 replicates each side — same standing caveat as the
`torch.compile` numbers above. This closes out the last open proposal from the original
4-dimension optimizer pass.

**General rule, confirmed on 1000genome-workflow `individuals.py` (2026-07-26):** when a
dftracer trace shows `comp="io"`-tagged span time vastly exceeding the underlying raw POSIX
syscall time inside those same spans (here: 1,861.8s io-tagged vs only 401.9s actual POSIX,
i.e. ~78% of "I/O" time was Python-level loop/parse overhead, not syscall latency), look for
loop-invariant work being recomputed per-iteration (per-individual, per-row, etc.) BEFORE
reaching for a compiled-library/vectorization rewrite -- hoisting the invariant work out of
the loop (`precompute_rows()`-style single pass) captured -93.4% standalone here, an order of
magnitude larger than the paired I/O fix. Also: **re-measure a previously-measured I/O fix
after a compute fix lands** -- its relative share of remaining wall time can GROW even though
its absolute savings stay flat, because the denominator (total time) shrank faster than the
I/O fix's own cost did (tar-streaming here: -13.2% of the ORIGINAL total standalone, but
removed 5.5s of the remaining 6.0s once parse cost collapsed -- i.e. it became relatively far
more important after the compute fix, not less).

**Use `grep -a` on run logs that might contain binary/core-dump bytes.** Confirmed on
ray_molformer (2026-08-02): a run log containing embedded core-dump/binary bytes (from a
crashed process's output mixed into the same log file) makes a plain `grep` silently return
no matches on that section, even when the surrounding text content is exactly what you're
searching for -- `grep` treats the file as binary once it hits non-text bytes and stops
matching normally. This can cause a genuinely successful run to be misread as a failure (or
vice versa) if you're grepping for a success/completion marker downstream of the binary
content. Always use `grep -a` (treat as text) when scanning run logs for HPC jobs, especially
ones where a ROCm/HIP/CUDA crash or core dump might have written raw bytes into the same
stdout/stderr stream as the rest of the log.

## Size the compute bottleneck against the APP's own timer, not the trace bucket (2026-08-05, RAJAPerf/MI300A)

Generalizes the PECAN "Layer Breakdown can hide a traced compute cost" lesson in the
opposite direction: a trace bucket can also massively OVER-state a compute bottleneck.

- GPU/HIP/CUDA API tracing cost scales with CALL COUNT, so it is worst precisely on the
  many-small-kernel-launch pattern that launch-overhead diagnosis targets. On RAJAPerf
  (MI300A, ROCm 7.2.1) dftracer HIP tracing inflated the two most launch/sync-heavy kernels
  **6.1x-6.7x** in the app's own reported time, producing a 26.8 s "compute bottleneck"
  that was ~85% observer effect and reversing the sign of a GPU-vs-CPU comparison.
- **Rule:** whenever a compute bottleneck is attributed to launch/sync overhead, get an
  UNTRACED measurement from the application's own timer before estimating any
  `app_impact_pct`. Keep tracing state identical across optimization ARMS (KB hard rule),
  but do the bottleneck SIZING untraced. Quote the denominator you used.
- **Corollary for busy-wait/spin knobs** (`HSA_ENABLE_INTERRUPT=0`, `cudaDeviceScheduleSpin`,
  `HIP_LAUNCH_BLOCKING` variants): they trade host CPU for sync latency. On a unified/APU
  system, or any app that also runs host-side compute on the same cores, the CPU cost can
  exceed the GPU gain - measure the CPU-side kernels too, not just the GPU ones. Measured
  RAJAPerf result: GPU -1.0%, CPU +4.0%, net +3.8% WORSE.
- **Interleave arms and require >=5 replicates before crediting a launch-overhead delta.**
  A single-replicate probe of `HSA_ENABLE_INTERRUPT=0` showed -9.9%/-16.4%; over 5
  interleaved replicates the same knob showed -3.4%/+0.8% with overlapping ranges. Wall
  time per identical run drifted 150 s -> 82 s across a 35-minute window purely from
  allocation contention - a back-to-back A/B design would have manufactured a fake result.
