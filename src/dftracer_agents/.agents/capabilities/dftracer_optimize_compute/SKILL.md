---
name: dftracer_optimize_compute
description: Compute component of the dftracer optimizer - the 10-dimension compute checklist (vectorization, kernel/library selection, overlap, compiler/runtime flags, threading, auto-tuning, frequency/power, NUMA/affinity, hardware capability), L1/L2/L3 strategies for compute_time, built-in citations and measured PECAN/vpic-kokkos/RAJAPerf/1000genome findings, with a validator for the citation-backed findings table. Load for compute_time, cpu_bound, gpu_util or flops bottlenecks; a Layer Breakdown that shows zero compute although compute events were logged; an app running 1 OpenMP thread per rank with idle cores; AMP vs torch.compile vs kernel-fusion ranking on MI300A/ROCm; per-step .cpu()/.item() host-device syncs; HIP/CUDA tracing inflating launch-bound kernels; or busy-wait knobs (HSA_ENABLE_INTERRUPT=0).
---
Cross-references: [[dftracer_optimize]], [[dftracer_opt_kb]], [[dftracer_papers]], [[dftracer_analyze]], [[dftracer_diagnose]], [[dftracer_optimize_io]], [[dftracer_optimize_communication]], [[dftracer_optimize_memory]], [[software-rocm]], [[system-tuolumne]], [[workload-vpic-kokkos]], [[workload-1000genome]]

# dftracer_optimize_compute

The compute component of the optimizer pipeline, sibling of [[dftracer_optimize_io]] (converted from the dftracer-agents `dftracer-compute-optimization` skill and the `dftracer-optimizer-compute` agent). The same rules apply - citation-backed, never "do less", never change the app's actual computation as a "pattern swap" unless correctness is verified byte-for-byte - plus the compute-specific catalog. The metric key used by the MCP optimization tools is `compute_time` (`_L1_STRATEGIES`/`_L2_STRATEGIES`/`_L3_STRATEGIES["compute_time"]` in `mcp_tools/tools/optimizations/strategies.py`).

## Rules

- **First step: `opt_compute_checklist(bottlenecks_json=<diagnosis>)`** (dimensions, built-in L1/L2/L3 `compute_time` strategies, default citation Roofline, the compute bottlenecks). **Last step: `dftracer_optimize_compute_check(findings_json=<one row per dimension>)`** must return `pass` before the findings go back to the orchestrator.
- **Exhaustive Dimension Checklist (walk ALL, every session).** Never stop at the first compute fix that helps. Record a verdict for EACH category (Applied & measured / Applicable, not measured / Not applicable - with reason), exactly like the I/O checklist:
  1. **L1 algorithmic/vectorization** - SIMD/vectorized loops, batched tensor ops, blocking/tiling for cache reuse, mixed precision (fp16/bf16/tf32) where numerically safe.
  2. **L1 kernel/library selection** - swap a naive implementation for a vendor-tuned kernel (BLAS/LAPACK, MIOpen/rocBLAS, cuBLAS/cuDNN) without changing the algorithm's output.
  3. **L1 compute/I-O or compute/comm overlap** - async execution so compute doesn't idle-wait on data movement (see the communication and memory capabilities for the other side).
  4. **L2 compiler/runtime flags** - `-O3`, LTO, target ISA flags (`-march=native`/`-mcpu=`), math-library thread count (`OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `ROCBLAS_LAYER`) matched to physical core/CU count (never oversubscribed).
  5. **L2 threading model** - OpenMP schedule (`static`/`dynamic`/`guided`), thread pinning/affinity, GPU stream concurrency.
  6. **L2 auto-tuning** - vendor kernel auto-tuners (MIOpen find-mode, cuDNN benchmark mode) that pick the fastest variant for the actual problem shape without altering results.
  7. **L3 CPU/GPU frequency & power** - performance governor, GPU clock/perf-level profile; check it is user-tunable (often admin-only) first.
  8. **L3 NUMA/affinity binding** - pin compute to the NUMA node/CU set holding its working set (shared with the memory checklist's NUMA dimension: record once, cross-reference).
  9. **L3 hardware capability check** - confirm the target has the vector/tensor unit the technique assumes (AVX-512, matrix cores) before proposing it.
  10. **Compute/communication overlap** - overlap collectives with the backward pass or independent compute (shared with the communication checklist).
  Per category run the literature search (arXiv/Semantic Scholar/`rag_search`/`opt_kb_lookup`) before marking "not applicable" for lack of a technique. Never silently omit a category.
- Every row in the resulting `opt_proposal_table` MUST carry `app_impact_pct` and `system_impact_pct` (0 if not measured/applicable) - columns `#`, `Strategy`, `Description of Optimization`, `App Impact`, `System Impact`, `Weighted Score` (50/50, auto-sorted by the tool). Never omit these fields.
- **Tool-first.** `opt_kb_lookup` (for THIS component) -> `session_search_optimization_context` (step "0.5", every iteration) -> `session_generate_optimization_proposals` -> `session_optimize_l1_app` / `session_optimize_l2_software` / `session_optimize_l3_filesystem` with metric `compute_time` -> `session_optimization_iteration` -> `comparator` -> `session_search_optimization_papers` / `rag_search` -> `search_arxiv` / `search_semantic_scholar`. Tools unavailable -> ask the user to start the dftracer MCP server; tools erroring -> fix the tool or wiring before custom Bash/Python. Report TOOL FINDINGS and MANUAL ANALYSIS in separate, labelled tables.
- **Independent literature/KB search pass** every iteration: KB recall, then an unconditional search for techniques matching the observed compute kernel shape even if the diagnosis does not rank it; both feed the proposal table; every literature-sourced technique tried is `opt_kb_record`ed with its citation and a "literature-pass finding" note.
- **Ordering: compute is optimized LAST** in the canonical I/O -> communication -> memory -> compute order (`_category_sort_key` in `strategies.py`); confirm via the diagnosis or a quick roofline check that compute time is a significant fraction of total time before investing. Within the component, L1 -> L2 -> L3; prefer compiler-flag/threading tuning (lower risk) before kernel/library swaps.
- **The optimization loop**: recall + exhaustive search; `opt_proposal_table` with EVERY checklist candidate, applied or not, one-line reason for each not-applied row; apply ONE change, re-run at the SAME scale, measure, `opt_kb_record` (no-ops and regressions too, scope chosen honestly); `opt_kb_render`.
- **metric_scope axis and non-degradation guard**: `metric_scope="system"` records need the paired `app_metric`/`app_before`/`app_after`; `guard_triggered: true` (app regressed > 2%) is a regression: revert.
- **Validate** every applied optimization by re-running: identical output/op count with better compute time = a real, safe win.
- **Replicates**: >= 5 per iteration before `comparator`/`opt_kb_record` (CV > ~10-15% -> 8, then 10); compare p50, state p95/min/max; record medians with the band and replicate count in `notes`; interleave arms.
- **Python apps**: when io-tagged span time vastly exceeds raw POSIX syscall time, look for loop-invariant work to hoist before a compiled-library rewrite; re-measure a previously measured I/O fix after a compute fix lands (its relative share can grow).
- Load first: `graph_query(mode="docs", question="dftracer-system-detect")` for hardware capability (vector/tensor units, GPU/APU architecture) and the framework/math-library layer skill if any.
- **Step profiling**: `profile_step_begin(step="STEP N: dftracer-optimizer-compute", agent="dftracer-optimizer-compute")` ... `profile_step_end(...)`; never `profile_bind`.
- **Return**: iteration table, FULL checklist verdict table, best config, honest note on what was not verifiable; propose new pitfalls/recipes to the main thread for user confirmation, never write lesson/skill files directly.

## Constraints

- **Never change the app's actual computation as an "optimization".** Do not swap an algorithm for a numerically different one, reduce solver precision/tolerance, skip compute steps, or reduce epoch/iteration counts because the alternative measured faster: that is doing less or changing correctness. Kernel/library swaps and vectorization are safe ONLY when output is verified byte-identical (or within the algorithm's own documented numerical tolerance) before/after - mark such rows `output_verified: true`.
- **Never "do less"**: no fewer checkpoints, less data, fewer epochs, skipped synchronization/verification the app's correctness depends on, reduced precision or correctness.
- **Size the bottleneck against the APP's own untraced timer** whenever it is attributed to launch/sync overhead; keep tracing state identical across optimization ARMS, but do the SIZING untraced and quote the denominator.
- Never bundle changes; one lever per measurement.
- Allocation rules, run length (>= ~10 min; for DL a fixed epoch count from the user's time budget), environment consistency and shared-source-tree rules are as in [[dftracer_optimize_io]] (Constraints), unchanged for compute. When a sibling runs in parallel on the same source tree, pin known env-gates of the sibling's changes (e.g. `PECAN_TORCH_COMPILE`) explicitly in your run script.
- Locate with graphify/`graph_query` rather than reading whole trees; logs go to artifacts; capture the run record before finishing; redact before persisting.

## Corner cases

- **The analyzer Layer Breakdown can hide a fully traced compute cost** (PECAN, 2026-07-20): `posix`/`dlio`/`generic` presets have no `compute` bucket, so it shows zero although `cat=compute` events exist. Count them with `event_count` / `view` / `aggregator` (query `cat == "compute"`) before concluding compute is untraced or free.
- **The opposite: a trace bucket can massively over-state compute** (RAJAPerf, MI300A, ROCm 7.2.1, 2026-08-05): HIP API tracing cost scales with call count and inflated the two most launch/sync-heavy kernels 6.1x-6.7x, a 26.8 s "bottleneck" ~85% observer effect that reversed a GPU-vs-CPU comparison.
- **Busy-wait/spin knobs** (`HSA_ENABLE_INTERRUPT=0`, `cudaDeviceScheduleSpin`, `HIP_LAUNCH_BLOCKING` variants) trade host CPU for sync latency; on an APU or an app with host-side compute the CPU cost can exceed the GPU gain - measure CPU-side kernels too (RAJAPerf: GPU -1.0%, CPU +4.0%, net +3.8% worse).
- **Tiny-hidden models are launch-bound** (PECAN EGNN, `hidden_nf=20`): read the model's hidden width before ranking AMP (fewer FLOPs) vs kernel fusion (fewer launches) - but a launch-bound hypothesis is not a substitute for a matched, de-confounded measurement (torch.compile measured zero steady-state gain).
- **Epoch-1 -> epoch-2 page-cache warmup confounds** compile/caching effects: compare matched epochs (e.g. 3-epoch baseline vs 3-epoch variant).
- **Per-step `.cpu()`/`.item()` host-device syncs** are a common serializer, but audit how pervasive they are before calling the fix low-risk; `torch.FloatTensor([x.to(device) for x in batch])` builds a CPU tensor and discards the device work; `torch.stack` needs `.view(())` to keep `(B,)` shapes.
- **App-reported thread counts can lie** (VPIC's legacy `n_pipeline` stayed 1): verify thread changes by measured wall time.
- **Not-applicable swaps that change values** (`radius_graph`/`knn_graph`, `pairwise_distances` -> `torch.cdist` O(E) vs O(N^2)) are forbidden pattern swaps; MIOpen autotune is not applicable to models without convolutions.
- NUMA/core-affinity was a confirmed no-op on the Tuolumne MI300A system for a third workload class (system-tuolumne): do not re-propose it without a different mechanism.
- Run logs that may contain core-dump/binary bytes: use `grep -a`, or a plain `grep` silently stops matching and a successful run can be misread as failed.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector

`dftracer_optimize_compute_check` names the failed check; repair, re-run `opt_compute_checklist` if needed, then check again. The same failure twice stops the loop and goes to the user.
- `findings_parse` -> pass a JSON list of rows, `{"rows": [...]}`, or a path, with the `row_fields` from `opt_compute_checklist`.
- `findings_present` -> record one row per checklist dimension.
- `every_dimension_has_a_verdict` -> add rows for the missing dimension numbers (1-10), each with a verdict and, if not applied, a reason after the literature search.
- `verdicts_valid` -> applied | applicable | not_applicable.
- `level_is_L1_L2_L3` -> name the layer of each applied/applicable row.
- `level_matches_dimension` -> dimensions 1-3 are L1, 4-6 L2, 7-9 L3 (10 any): fix the dimension or the level.
- `citation_backed` -> cite a paper/DOI/arXiv id, docs, a built-in key (roofline, ...) or `session:<run_id>`; search via [[dftracer_papers]].
- `reason_for_not_applied` -> give a concrete technical reason (hardware lacks the unit, admin-only governor, no convolutions to auto-tune, ...).
- `impact_fields` -> numeric `app_impact_pct` and `system_impact_pct` on applied rows, sized against the app's untraced timer.
- `never_do_less` -> drop rows that reduce epochs/steps/inputs/output; keep the work.
- `component_rules` -> a kernel/library/algorithm swap without `output_verified: true`: verify the output before/after (byte-identical or documented tolerance) and set the flag, or mark the row not applicable.

## Self-heal

- If `opt_compute_checklist` or `dftracer_optimize_compute_check` is wrong, fix it with capability_update; do not work around it in shell.
- If an MCP optimization tool errors, fix it or its wiring before manual work, and label manual results MANUAL ANALYSIS.

## Self-learn

- What is explored here becomes a capability: see capability_builder. Every measured compute result is `opt_kb_record`ed (including no-ops and reversals like torch.compile on PECAN); new pitfalls become lessons here; workload/system facts go to their workload_/system_ capabilities after the user confirms.

## What works

- 2026-07-14 (vpic-kokkos, Kokkos-OpenMP, 128 ranks / 8 nodes, Tuolumne): `flux run -c6 ...` plus `OMP_NUM_THREADS=6 OMP_PROC_BIND=spread OMP_PLACES=cores` (runtime env only, no rebuild) cut wall time 41% (210.2 s -> 124.0 s).
- 2026-07-22 (PECAN, 4N x 16-rank DDP, MI300A): loss-block host-device sync rewrite (`torch.stack([x.to(device).view(()) ...])`, one `.item()`): epoch-2 -3.5% vs io_opt4_finalize, -9.0% vs baseline5, single replicate; bit-identical loss on a fixed-seed check.
- 2026-07-26 (1000genome `individuals.py`): hoisting loop-invariant parse work out of the per-individual loop (`precompute_rows()`) -93.4% standalone.

## Reference: L1 Application Strategies (metric: compute_time)

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
compute is untraced or free, count the `"cat":"compute"` events and sum their `dur` in the
compact traces through the dftracer-utils tools (`event_count`, `view`/`stats`/`aggregator`
with a `cat == "compute"` query, in [[dftracer_analyze]]) or TraceViewer** rather than trusting
the Layer Breakdown alone. (Converted 2026-11-13: the original recipe decompressed the
`.pfw.gz` files by hand; capabilities read traces only through dftracer-utils.)

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
