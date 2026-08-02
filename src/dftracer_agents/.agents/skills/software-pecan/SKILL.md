---
name: software-pecan
description: PECAN (Pose Classification, part of PECAN/MILAN pose-classification/binding-affinity codebase) — PyTorch+PyTorch-Geometric EGNN training on HDF5 shard data. Build, annotation status, dataset access, and measured dftracer optimization findings. Load this skill for any PECAN/PDBspheres/PDBBind session.
---

Cross-references: [[dftracer-io-optimization]] [[dftracer-compute-optimization]]
[[dftracer-communication-optimization]] [[dftracer-memory-optimization]] [[system-tuolumne]]

PECAN is annotated/instrumented by dftracer (this skill uses `software-` naming, not
`workload-`, per the software-vs-workload naming convention). Source repo:
`https://czgitlab.llnl.gov` (`kim63/pecan_milan`), sibling app MILAN in the same repo shares
this skill's build/env notes but has a separate (unannotated) codepath — see below.

## Codebase layout — which files are actually the baseline path

`pecan/trainer.py` + `pecan/dataset.py` (`Dataset_PDB`) are the ANNOTATED, ACTIVE baseline path
for the PDBspheres/PDBBind pose-classification workload. `milan/*.py`, `*_old.py`, and
`pecan/dataset_dyad*.py`/`pecan/datacopy_dyad*.py` (DYAD in-transit staging path) are NOT
annotated and NOT part of the default local-I/O run — don't be misled by grep/graph-BFS
neighbors into thinking they're in scope. `model/egnn.py` (the `nn_type=3` EGNN model) only
needs `torch_geometric` core, NOT `torch_scatter`/`torch_sparse` — but `model/model_trainer.py`
unconditionally imports ALL model variants at module load (including `sgcnn.py`/`ggcnn.py`,
which DO need `torch_scatter`/`torch_sparse`), so both extensions must still be built even
though only EGNN is exercised at runtime.

## Build (Tuolumne / MI300A / ROCm 6.3.1)

- torch ROCm wheels (`repo.radeon.com/rocm/manylinux/rocm-rel-6.3.1/`) only exist for
  cp39/cp310/cp311 — use `python/3.11.5` module, NOT the Tuolumne-default `python/3.13.2`.
- `torch_scatter`/`torch_sparse` (vendored source at
  `pyg-rocm-build/{pytorch_scatter-2.1.2,pytorch_sparse-0.6.18}` in the parent install repo,
  copy before building — never build against a read-only source tree) must be built with
  `PYTORCH_ROCM_ARCH=gfx942` (MI300A only) — without it, HIP compiles for 12 architectures and
  takes 30-60+ min per package instead of ~5-8 min.
- `torch_scatter` builds fine with Cray `mpic++` (`CC=mpicc CXX=mpic++`); `torch_sparse`'s CPU
  extensions use `-fopenmp` and crash at IMPORT time with `undefined symbol:
  _cray$mt_kmpc_fork_call_with_flags` when linked with Cray's OpenMP runtime — rebuild with
  `module load gcc-native/13; CC=gcc CXX=g++` (plain system GCC) instead.
- Both packages' `setup.py install` post-build step throws a spurious
  `error: [Errno 2] No such file or directory` on this filesystem (NFS-ish rename race) —
  harmless; the wheel is already in `dist/*.whl` by that point, `pip install` it directly.
- `pip install pyyaml` separately — not pulled in by any other PECAN dependency, but
  `main_app.py`'s `load_config()` needs it.

## Dataset access — a real per-file permission gotcha, not directory-wide

PDBspheres/PDBBind data on VAST (`$VAST_ROOT/data_pecan/`, or a differently-permissioned
copy at `$VAST_ROOT/...` owned by a different group). The pre-generated CSV index
(`pdbspheres_all_0000000_all.csv`) can be OWNER-ONLY `rw-------` even though the actual
`.hdf5` shard files in the SAME directory are group-readable `rw-rw----`. If the index CSV is
inaccessible, don't give up — pass `train_fns` (a list of `.hdf5` file paths) instead of
`train_csvs`; `Dataset_PDB.__init__`'s h5-driven fallback path derives the same metadata
directly from the HDF5 files and auto-generates its own index CSV.

## Real app bugs found and fixed (2026-07-20, keep this code path fixed going forward)

1. `Dataset_PDB.__init__`'s h5-driven path writes its auto-derived index CSV back into the
   SAME directory as the source `.hdf5` files (`fn_prefix + "_all.csv"`) — always fails with
   `PermissionError` when the dataset is on a read-only-to-this-session PFS mount. Fixed via a
   `PECAN_INDEX_CSV_DIR` env var (falls back to original behavior, then `tempfile.gettempdir()`
   on `PermissionError`) so the derived index lands in a writable location instead.
2. The crystal-structure branch of `Dataset_PDB.__init__` (h5-driven path, `use_crystal=True`)
   appended a 6-element list while `__getitem__` unpacks 7 — crashed with `ValueError: not
   enough values to unpack (expected 7, got 6)` the first time a crystal-structure entry was
   iterated. Fixed by adding the missing trailing `0.0` (score) field.

## DDP launch on Tuolumne (flux)

- `flux run -N<nodes> -n<total_tasks> -g<gpus_per_task>` is the ONLY valid flag combo — mixing
  `--tasks-per-node` (a "per-resource" option) with `--gpus-per-task` (a "per-task" option)
  errors `Per-resource options can't be used with per-task options`.
- `MASTER_ADDR` MUST be resolved identically on every rank — `flux getattr local-uri` returns a
  `local://` socket path, NOT a hostname; using it (or each rank's own `$(hostname)`) makes
  every rank compute a DIFFERENT `MASTER_ADDR`, silently hanging NCCL/RCCL rendezvous forever
  with ZERO output (Python's stdout is block-buffered when non-tty, so a live-but-hung job
  looks identical to a not-yet-started one). Correct recipe (every rank agrees on the same
  first-node hostname):
  ```bash
  NODELIST=$(flux job info $FLUX_JOB_ID R | python3 -c \
    "import json,sys; print(json.load(sys.stdin)['execution']['nodelist'][0])")
  export MASTER_ADDR=$(flux hostlist -n 0 "$NODELIST")
  ```
- Always run multi-rank flux-launched Python with `python -u` (unbuffered) — a live 16-rank job
  can look completely silent for 1-2 minutes while torch/torch_geometric/h5py/dftracer import
  across the network filesystem on all ranks concurrently; without `-u` you can't tell "slow
  import" from "hung."
- **`dftracer_service` node-counter daemon — use `-n<n> -c1`, NOT `--tasks-per-node 1`,
  AND never launch it with `flux run` at all (corrected 2026-07-29).** The earlier
  recipe below (`flux run -N<n> -n<n> -c1 dftracer_service start <dir>`) fixed the
  `--tasks-per-node`-implies-exclusive problem, but `flux run start ...` itself
  **never returns** — the daemon forks and stays a child of the flux task, so the task
  holds its node(s) forever and the training-job phase never launches. This looks like
  it worked (the per-node `dftracer_server_<hostname>.pid` files are written correctly,
  zero errors), but the wrapper script just hangs at that line. See
  `bug-dftracer-service-start-blocks-flux-run` for the full incident (observed on
  dftracer 2.1.0.dev16, Tuolumne/Flux: inner job sat `R` for 5+ minutes, all 4 pid files
  present, zero progress).

  **Correct, validated recipe: `flux submit` (detached) + poll for the pid files, then
  `flux submit` again to stop:**
  ```bash
  # start (detached — returns immediately, does NOT block the wrapper)
  SVC_JOB=$(flux submit -N<n> -n<n> -c1 dftracer_service start <dir>)
  for _ in $(seq 1 30); do
    [ "$(ls <dir>/dftracer_server_*.pid 2>/dev/null | wc -l)" -ge <n> ] && break
    sleep 2
  done
  # ... run the training job phase here ...
  # stop, same detached pattern, in an EXIT trap so it always runs
  flux submit -N<n> -n<n> -c1 dftracer_service stop <dir>
  ```
  The `-n<n> -c1` part (never `--tasks-per-node`, which silently reserves the nodes
  exclusively) is still correct and still required — only the `flux run` -> `flux submit`
  change is new. `DFTRACER_ENABLE` and `DFTRACER_LOG_FILE` must still be exported in the
  same invocation or the daemon silently no-ops, and a non-empty per-node trace must be
  verified afterwards. Verify with `flux job info <id> jobspec` (no `"exclusive": true`)
  and `flux resource list` before assuming co-location works. Also confirmed: the
  per-hostname `.pid` file fix in dftracer's `3e6fc42` develop commit
  (`dftracer_server_<hostname>.pid`, replacing a single shared `dftracer_server.pid` that
  used to cause "No running server found") is real — `stop` cleanly sends SIGINT to every
  node's server once combined with the non-exclusive, detached invocation above.
- **Before crediting any optimization-variant wall-time delta, verify the variant run matches
  the baseline's SCALE (rank/process count) and actually COMPLETED (no `job.exception cancel`
  in its event log).** A half-scale or cancelled run's wall time is worthless for comparison —
  fall back to work-normalized per-operation ratios (e.g. opens-per-`__getitem__`), which are
  scale- and truncation-robust, when a clean equal-scale re-run isn't available before the
  allocation expires.

## Measured dftracer findings (2026-07-20, 16-rank / 4N x 4GPU DDP, PDBspheres_v2_split8,
150 shards, batch_size=8, num_workers=2, 2 epochs)

Diagnosed bottleneck ranking (real `diagnose()` output, `analyzer_preset="generic"`):
**hdf5 layer CRITICAL and worsening** (severity_score=0.988, prevalence=73%) — dominated by
`H5Oopen` (577s cumulative, 4.6M calls) and `H5Fopen` (490s cumulative, 48,384 calls) —
i.e. METADATA/OPEN overhead from `Dataset_PDB.__getitem__` re-opening the same small HDF5
shard files fresh on every call, not read bandwidth (`H5Dread` itself only 58s). **Fix: a
per-worker `h5py.File` handle cache** (`_get_h5`, keyed by filepath, with `__getstate__`
dropping the cache dict so no fd crosses the DataLoader fork) + `persistent_workers=True`.
Validated mechanistically: **-76.5% file-open rate** (work-normalized `H5Fopen`/`H5Aread`
ratio, robust to the scale/truncation issues that affected the wall-time measurement — see
the flux-alloc note above). `communication_io` (severity 0.996) is the SAME root cause via a
different `dft_event_logging` wrapper around the same code — expect the handle-cache fix to
resolve both together.

GPU compute (`model-forward`+`model-backward`, `cat=compute`) was the single LARGEST per-rank
cost once measured directly (776s aggregate, bigger than the entire 412s preprocess bucket) —
it did NOT show in any analyzer preset's Layer Breakdown (posix/dlio/generic all lack a
`compute` layer bucket entirely; see `dftracer-compute-optimization`), which looked like "zero
compute cost" but was a preset-bucketing artifact, not a tracing gap. Compute optimization
candidates ranked by potential (none measured this session — see `dftracer-compute-optimization`
for the full table): AMP bf16 autocast (gfx942 native bf16) > torch.compile (risk: PyG dynamic
shapes) > precompute/cache graph adjacency to disk (invariant across epochs, no augmentation)
> MIOpen autotune > `torch.cdist` swap for `pairwise_distances`.

`communication-except-io`/`cpu-gpu-transfer` and NUMA/memory levers are all confirmed NOT the
bottleneck for this app (25.58s aggregate comm cost = <1% of wall; MI300A unified HBM makes
`pin_memory`/staging-copy tuning structurally inert; NUMA/launcher affinity confirmed inert on
this system for a 4th workload class by inference from prior KB). Do not re-investigate these
dimensions for PECAN without new evidence — I/O (HDF5 metadata storm) dominates by 2+ orders
of magnitude and should stay the primary optimization target.

## Full 4-dimension optimizer pass (2026-07-21, baseline5 trace, post-handle-cache, 15 real
`diagnose()` findings, 14 high/critical)

**I/O — second-order HDF5 metadata storm confirmed UNDER the handle-cache fix.** The per-worker
file-handle cache eliminated `H5Fopen` (1.0 → 0.03/sample, verified) but HDF5 remains the #1
persistent finding (94.7M events / 2020.87s aggregate, persistence=15 — present across nearly
the whole run). Root cause is now CLIENT-SIDE h5py PER-CALL OVERHEAD, not file opens:
`H5Oopen` object navigation (~16/sample, `h5[pdbid][h5_dcom][poseid]` group traversal) + 5
scalar-attribute reads every `__getitem__` (`num_hbonds/hpbond/habond/sbond/pbond`, each
dragging `H5Aopen_by_name`+`H5Aget_type`+`H5Aget_space`+`H5Sget_simple_extent_dims`+`H5Aread`+
`H5Tclose`+`H5Sclose`) + ubiquitous h5py bookkeeping (`H5Eset_auto2` 6.2M calls = ~51% of all
HDF5 events; `H5Iis_valid`/`H5Idec_ref` ref-counting). Next levers (cited Mohan et al., VLDB
2021, https://arxiv.org/abs/2007.06775), NOT YET APPLIED — need a validation run before
crediting: (a) hoist the 5 scalar attrs into `data_list` at `__init__` time so `__getitem__`
reads from the list instead of `h5_data.attrs` (removes ~40-50% of per-sample metadata events
every epoch); (b) per-worker decoded-sample cache keyed by `(pdbid, poseid)` for epoch-2+ reuse
(samples are invariant in graph modality — no augmentation there; must guard OFF for
`modality==1`, which DOES apply random affine augmentation per sample). `stdio_ops_slope`
scored critical (0.999) but is a BURST-RATE ARTIFACT, not real overhead — 0.85s total = 0.008%
of wall time, 49,919 cheap `fopen64`/`fclose` probe events. Do not act on it.

**MEASURED 2026-07-22 (baseline5 scale, 4N×4GPU DDP, live validation run, `annotated/source/pecan/dataset.py`):**
both (a) attr-hoist and (b) decoded-sample cache were applied TOGETHER (not isolated —
allocation time ran out before a per-proposal breakdown). Correctness verified standalone
first: 0 mismatches across 50 rows comparing hoisted attr values against direct `h5py.attrs`
reads. The `csv_filepaths` init branch was confirmed OUT OF SCOPE and left untouched —
`train_csvs: null` in this run's config, so that code path is dead for this workload; the
decode-cache guard for `modality==1` augmentation is inert for the same reason (active config
is `nn_type: 3`, graph modality, no per-sample augmentation) but was kept in place for safety.
Combined measured delta: epoch-2 (steady-state) wall time 31.54s → 28.77s (**-8.8%**);
`data-load-h5` event count -49.1% (26,112 → 13,280), mean event duration -14.5%. (Epoch-1 delta
was NOT used as the primary metric — confounded by `__init__` overhead and a concurrent job
sharing the allocation.) Single replicate — treat as directional; a clean isolated #1-vs-#2
breakdown and more replicates are still needed before fully crediting either sub-change.

**Incident note (2026-07-22):** running the I/O and compute optimizer agents in PARALLEL on
separate allocations against the SAME shared `annotated/source/` tree caused one NCCL
`remote process exited` crash — the compute agent's concurrent edit to
`model/model_trainer.py` (adding `torch.compile`) meant different ranks in the I/O agent's job
loaded different versions of that file mid-run, breaking DDP sync. Recovered by setting
`PECAN_TORCH_COMPILE=0` (an env-gate the compute agent had already added) rather than touching
the other agent's file. See `feedback-shared-source-tree-race` for the general lesson.

**MEASURED 2026-07-22 (isolation run, hoist-only, baseline5 scale, 4N×4GPU DDP):** added a
`PECAN_DECODE_CACHE` env-var gate (default "1" = prior combined-on behavior, no-op unless set)
around the per-worker decoded-sample cache in `_get_h5`/`__getitem__` so proposal #2 (decode
cache) could be disabled independently of proposal #1 (attr-hoist, left always-on). With
`PECAN_DECODE_CACHE=0` (attr-hoist ONLY): epoch-2 wall time 31.544s → 33.146s (no measured win,
+5.1%, single replicate/noisy); `data-load-h5` event count 26,112 → 26,336 (essentially
unchanged, +0.9%) — expected, since attr-hoist only shortens per-event work inside
`__getitem__`, it does not skip/merge events like the decode cache does. **By subtraction
against the combined measurement (both proposals: -8.8% wall time, -49.1% event count), the
decode cache (proposal #2) accounts for essentially ALL of the measured win; attr-hoist alone
showed no resolvable wall-time benefit at 1 replicate.** Treat as directional — needs ≥5
replicates on an isolated (non-shared) allocation before fully crediting either sub-change; do
not yet conclude attr-hoist is worthless, only that its effect (if any) is smaller than this
run's noise floor.

**Pitfall confirmed again (2026-07-22):** the first isolation-run attempt hit the exact
shared-source-tree race from the incident note above — `PECAN_TORCH_COMPILE` defaults to "1" in
`model/model_trainer.py`, and a concurrently-running compute-optimizer job on a different
allocation was mid-edit on that file, causing an `InternalTorchDynamoError:
ModuleNotFoundError: No module named 'torch.distributed.tensor._ops'` on some ranks and a
cascading NCCL `DistBackendError`. Fix: always export `PECAN_TORCH_COMPILE=0` explicitly in any
I/O-optimizer run script while a compute-optimizer job may be running in parallel against the
same `annotated/source/` tree, rather than relying on the default.

**RESOLVED 2026-07-22 (replicate confirmation, same allocation window as the isolation run
above):** ran one more replicate of the combined config (`io_opt1_r3`: 28.953s, 13,056
data-load-h5 events) and two more replicates of the hoist-only config (`hoistonly_r2`:
31.9998s/26,112 events; `hoistonly_r3`: 32.5846s/26,112 events).

- **Combined (both proposals ON) — CONFIRMED.** 2 replicates: 28.766s and 28.953s
  (mean 28.859s, CV 0.65%). Delta vs. the single baseline5 measurement (31.54s) is -8.5%
  (range -8.8% to -8.2%) — the originally-reported -8.8% win is real, not single-replicate
  noise.
- **Hoist-only (attr-hoist alone, `PECAN_DECODE_CACHE=0`) — CONFIRMED NO WIN, revised from
  "noisy/directional".** 3 replicates: 33.146s, 32.000s, 32.585s (mean 32.577s, stdev
  ~0.57s, CV 1.8% — low noise). All three land at or above the 31.54s baseline (mean +3.3%);
  none show a wall-time improvement. `data-load-h5` event count is deterministic and
  IDENTICAL across all 3 hoist-only replicates (26,112) and matches baseline5's own count
  exactly — consistent with attr-hoist only shortening per-event work inside
  `_get_h5`/`__getitem__`, not skipping/merging events, so it cannot move event count.
- **Conclusion, now resolved (not just directional):** the decode-cache (proposal #2)
  accounts for essentially ALL of the combined -8.5% win; attr-hoist (proposal #1) alone
  provides no measurable wall-time benefit at this workload/scale. Do not credit attr-hoist
  as a standalone win; it is safe to keep (0 correctness regressions, per the earlier
  standalone check) but should not be reported as a performance optimization on its own —
  only in combination with the decode-cache, or as groundwork/no-cost prerequisite for it.

**Communication — the "worsening `communication_io`" signal is an ARTIFACT, not a new
bottleneck.** `communication_io` (a `dft_event_logging` wrapper, 2690.17s aggregate) simply
CONTAINS the same HDF5 storm above (2020.87s of it is the nested `hdf5` layer) — it is not an
independent communication cost. The "worsening" trend is inflated by low-prevalence warmup
windows (prevalence=0.08); steady-state per-window slope is flat/declining. Pure gradient/RCCL
comm (`communication_except_io`) reconfirmed negligible at this checkpoint too: 30.68s
aggregate = <1% of wall. `communication_io` resolves WITH the I/O fixes above; do not add a
separate communication-layer fix for it. General lesson: for wrapper-annotated DL dataloaders,
always check whether a "worsening comm" layer nests an I/O storm before treating it as an
independent communication bottleneck.

**Compute — code reading REVISES the prior ranking.** `model/egnn.py` uses `hidden_nf=20`, so
every `nn.Linear` GEMM in the 4-layer EGNN (`edge_mlp`/`node_mlp`/`coord_mlp` + 2×
`unsorted_segment_*` scatter + `BatchNorm1d`) is ≤20-wide — LAUNCH/MEMORY-BOUND, not
FLOP-bound. This DEMOTES AMP bf16 (prior #1 guess, ~12% estimate; revised ceiling ~2% given
tiny GEMMs + `BatchNorm1d` + the CPU-cast loss path below) and PROMOTES kernel fusion via
`torch.compile(model, dynamic=True)` (fewer kernel launches; risk: PyG's data-dependent edge
count via the distance-cutoff filter at `egnn.py:206` may trigger recompiles/graph-breaks —
must measure graph-break count + loss parity before crediting any speedup). Also surfaced: a
real per-step host-device SYNC SERIALIZER in `trainer.py`'s loss computation — the loss/label
tensors are moved `.cpu()` repeatedly (main loss + up to 5 bond-losses + a debug branch + the
logged `loss_val`), and `output_aff`/`output_rmsd`/etc. are ALREADY silently forced onto CPU by
`torch.FloatTensor([x.to(self.device) for x in batch])` (line ~104-113) — the `.to(self.device)`
inside that list comprehension is discarded work, since wrapping a list of GPU scalars in
`torch.FloatTensor(...)` materializes a NEW CPU tensor anyway. This is MORE pervasive than a
simple "2 syncs/step" fix — it's an entangled multi-sync block spanning the whole loss/backward
region — so **do not blind-edit it**; it needs a full loss-block rewrite (keep everything
device-resident, one `.item()` only for the printed/logged value) validated against a
byte-identical-loss check before crediting any wall-time delta. `torch.cdist` swap for
`pairwise_distances` is CONFIRMED NOT APPLICABLE (forbidden pattern-swap) — `egnn.py:204`'s
`PairwiseDistance` is already per-EDGE (O(E), gathered by `edge_index`), while `cdist` computes
a full O(N²) pairwise matrix; swapping would change the actual computed values, not just
performance. MIOpen autotune is not applicable — EGNN has no convolution kernels to tune.

**Memory — confirmed NOT memory-bandwidth-bound**, consistent with the prior pass; the fresh
15-finding diagnosis surfaced no memory bottleneck. One APPLIED (2026-07-21, zero-cost, folded
into `baseline5/scripts/run_baseline5.sh`, not yet re-measured): `PYTORCH_HIP_ALLOC_CONF=
expandable_segments:True,garbage_collection_threshold:0.8` — targets HIP caching-allocator
fragmentation from PyG's variable per-sample graph shapes, a DIFFERENT mechanism than the
already-inert `pin_memory`/NUMA levers (upper bound ~2%, app is I/O-bound so low priority).
EGNN sparse gather/scatter cache-blocking/op-reordering (arXiv 2308.12093) is a research-grade
model rewrite, deprioritized under I/O dominance — not applied.

**MEASURED 2026-07-22:** re-A/B'd `PYTORCH_HIP_ALLOC_CONF=expandable_segments:True,
garbage_collection_threshold:0.8` against no-env-var, matched 3-epoch uncompiled config/scale
(`opt3_baseline3ep` vs `opt4_nomemenv`). No env: epoch2/3 avg=5.635s; with env: epoch2/3
avg=5.81s (**+3.1%, noise-level — no measurable win**). Consistent with the predicted ~2%
ceiling being too small to resolve from a single replicate; keeping or dropping this env var is
a wash at this scale. Leave it in place (harmless) but do not credit it as a real optimization.

**Status as of 2026-07-21: only the zero-cost memory env var has been applied to the run
script; NOTHING has been re-measured yet.** The I/O attr-hoist/sample-cache, the trainer.py
loss-sync rewrite, and `torch.compile` are all proposed-and-ranked but require a clean
equal-scale validation run (per the flux-alloc scale-verification lesson) before crediting any
delta — do not claim these fixes "worked" until that run happens.

## Compute/data/communication overlap — EXACT, MEASURED 2026-07-22: 0% overlap, real bottleneck

Event-level sweep-line interval-merge overlap analysis (per-rank, exact — not a bucket-level
bound) against baseline5's compact traces (48 processes, `job_time=144.03s`), computed by
merging each category's `[ts, ts+dur]` spans per rank and intersecting the merged sets between
category pairs:

| Pair | Total A (rank-sec) | Total B (rank-sec) | Overlap |
|---|---|---|---|
| `compute` vs `communication-io` (data-load) | 1454.05 | 1309.70 | **0.00s (0%)** |
| `communication-io` vs `communication-except-io` | 1309.70 | 22.27 | **0.00s (0%)** |
| `compute` vs `communication-except-io` | 1454.05 | 22.27 | **0.00s (0%)** |

**All 48 ranks, all three pairs: zero temporal overlap, exactly — this IS the classic Mohan et
al. VLDB2021 "data stall" (framed as GPU/compute idle time, not data idle time), and here it is
TOTAL, not partial.** The main training-loop process's critical path is strictly sequential:
load → compute → transfer, never concurrent, at the granularity of the top-level annotated
spans. 100% of the 1309.70 rank-seconds of data-loading time is time during which that rank's
`compute` is fully idle.

This does NOT directly test whether `preprocess` (which runs in separate DataLoader-worker
*processes*, different `pid`s from the main loop) is itself overlapped with compute in
wall-clock terms — worker-process CPU work can still overlap with the main process's GPU compute
even though the main process's OWN `communication-io` span never does. But it DOES show that
whatever prefetching the DataLoader workers are doing does NOT prevent the main-loop
`communication-io` wrapper call from fully blocking `compute` — i.e. the workers are not
successfully hiding their latency behind the previous batch's compute. This is consistent with
a genuinely stalling pipeline (insufficient `num_workers`/`prefetch_factor`, or workers
themselves I/O-bound on the underlying HDF5 read) rather than the "preprocess overlaps by
design" assumption from the earlier compute-optimization pass holding in practice. **This is a
real, quantified, unaddressed bottleneck** — none of the proposals applied so far (decode-cache,
attr-hoist, memory env var) touch the load/compute serialization itself, only the cost of each
phase individually. The natural next lever is increasing `num_workers`/`prefetch_factor` so the
DataLoader can genuinely prefetch batch N+1 while the GPU computes on batch N (not yet applied
or measured).

Note: this run's `compute` aggregate (1454.05 rank-seconds total across 48 ranks, ~30s/rank) is
NOT directly comparable to the earlier "776s aggregate" figure cited elsewhere in this skill —
different trace/replicate scope, not a contradiction, flagged for future reconciliation rather
than assumed identical.

## Overlap fix attempt (2026-07-22): prefetch tuning applied, overlap metric UNCHANGED — likely a tracing artifact

Per GLANCED-IO (Sinurat et al., HPDC '26, `resources/papers/HPDC26_GLANCED_IO.pdf`) and Mohan
et al. VLDB2021, added an env-gated `PECAN_PREFETCH_FACTOR` (default 2 = torch default, no-op
unless overridden) to all 4 DataLoader/DataListLoader constructions in `pecan/trainer.py`.
Validated (single replicate, baseline5 scale, num_workers held at 2): `PECAN_PREFETCH_FACTOR=4`
gives epoch-2 wall time 29.68s (vs 31.54s pre-decode-cache baseline — a real improvement).

**BUT** re-running the exact sweep-line overlap measurement against this new trace came back
EXACTLY 0.00s/0% again, identical to the pre-fix measurement. A genuine prefetch improvement
should move that number off zero — this is strong evidence the 0% overlap metric is a TRACING
ARTIFACT (likely a stale pid tag inherited across the DataLoader's fork() boundary, or event
flush timing that doesn't preserve true cross-process concurrency), not literal proof of a
fully serial pipeline. Do not re-report "0% overlap" as a confirmed bottleneck without first
root-causing pid/tid attribution across the fork boundary — this supersedes the earlier "0%
overlap, real bottleneck" framing pending that root-cause.

**CONFIRMED PITFALL:** raising `num_workers` from 2→6 on this app's `DataListLoader`
(torch_geometric, default `fork` context, model already on the HIP device BEFORE DataLoader
construction) HARD DEADLOCKS the job. Fix not yet applied: switch to
`multiprocessing_context="spawn"`, or construct the DataLoader before `model.to(device)`.
`num_workers=2` is the only validated-safe value until then.

## Overlap RESOLVED 2026-07-22 — root cause of the "0%" confirmed as a methodology bug, real number is ~72-77%

The 0% same-pid sweep-line overlap was indeed a methodology artifact (as suspected above), NOT
a real property of the pipeline. Root cause: `compute` and `communication-io` are annotated on
the SAME process's own timeline (the main training loop), so a per-pid interval-merge is
definitionally serial there regardless of whether OTHER processes (DataLoader workers) are
truly prefetching concurrently — same-pid overlap analysis was answering the wrong question.

Re-measured with the correct algorithm (dynamic p99-sized buckets + mandatory event-splitting
at bucket boundaries + max-busy-time-across-ALL-processes per bucket — see
[[dftracer-overlap-analysis]] for the full method and why naive versions of this either give a
false 0% or an impossible >100%): against baseline5's `compute` (114.14s total) vs
`communication-io` (106.28s total) events (via `view` tool, 32,656 events, p99 dur=0.412s →
interval_width=0.824s → 169 buckets):

- **Overlap ≈ 81.92s → 71.8% of compute time IS overlapped with data-loading across
  processes** (i.e. DataLoader workers ARE meaningfully prefetching concurrently with compute).
- **Remaining compute stall ≈ 28.2% of compute time (32.2s)** is genuinely NOT hidden behind
  data-loading — this is the real, quantified residual bottleneck, much smaller than the
  earlier (wrong) "100% stall" framing implied.
- This was measured on baseline5 (pre-prefetch-factor-fix trace); re-running this same
  algorithm against the `io_opt3` (post-prefetch-fix) trace to see whether the ~28% stall
  shrank further has NOT yet been done — natural next step.

## num_workers deadlock RESOLVED 2026-07-22 — spawn context fix validated, real -35.7% win, with two new open caveats

Switched `DataListLoader` (train+val, `nn_type==3`/EGNN path) to
`multiprocessing_context="spawn"` in `pecan/trainer.py` — resolves the previously-confirmed
fork+HIP `num_workers` 2→6 deadlock. Root cause: `ModelTrainer.__init__` moves the model onto
the HIP device BEFORE `train()` ever constructs a `DataLoader`, so `fork()`-ing after HIP
context init is unsafe; `spawn` avoids forking that address space. Validated (`io_opt4`,
4N×16-rank DDP, `num_workers=6`, `PECAN_PREFETCH_FACTOR=8`, all 96 spawned workers across 16
ranks constructed and trained cleanly, zero deadlock): epoch-2 (steady-state) wall time
**31.54s → 20.27s (-35.7%)** — beats even the decode-cache config (28.859s mean, -29.7%
further improvement on top). Single replicate — needs ≥5 reps before fully crediting, per
standing practice this session.

**Two new open caveats, do not treat this as fully resolved:**
1. **Epoch-1 cost exploded: 451.8s vs baseline's 82.05s (+450%).** `spawn` re-imports
   torch/PyG/h5py fresh in every one of the 96 spawned worker processes (vs `fork`'s
   copy-on-write inherited state) — a real one-time startup tax. Not yet amortization-analyzed
   against a longer real training run (a few-epoch run would dilute this; a short run would not).
2. **dftracer produced NO per-DataLoader-worker trace files under `spawn`** — only 16
   rank-level `app.pfw.gz`, unlike the `worker_N` sub-traces present under the default `fork`
   context in baseline5/io_opt1/io_opt3. This BLOCKS re-running the
   [[dftracer-overlap-analysis]] algorithm against this config — the wall-time win is measured
   directly from the app's own epoch-timer log line (independent of tracing) so the win itself
   stands, but whether overlap-% actually improved (the causal mechanism) remains
   UNVERIFIED pending that trace-capture gap being root-caused. See
   `dftracer-overlap-analysis`'s new "Known gap" section.

**2026-07-22: `DFTRACER_INIT=HYBRID` + `LD_PRELOAD=libdftracer_preload.so` tried, did NOT fix
the missing worker traces — gap #2 above remains open.** Tested against the `io_opt4` config
(num_workers=6, spawn, `PECAN_PREFETCH_FACTOR=8`) as `io_opt5`: both `DFTRACER_INIT=HYBRID`
and an `LD_PRELOAD`-only fallback (no HYBRID) produced the SAME 16 rank-level `-app.pfw.gz`
files and ZERO unnamed worker trace files as the un-preloaded baseline — no improvement.
`HYBRID` is not a recognized value in dftracer's `ProfileInitType` enum (only
`PROFILER_INIT_FUNCTION`/`PROFILER_INIT_LD_PRELOAD` exist in the source) and produced no
warning — it silently no-ops or falls back to FUNCTION-mode default. Notable asymmetry: the run
WITH `HYBRID` set finalized cleanly on all 16 ranks, while the `LD_PRELOAD`-only run (no
HYBRID) hung indefinitely at process teardown after the final checkpoint save (killed by a
900s timeout) — suggesting `HYBRID` does affect something (shutdown ordering?) even though it
produces no additional trace files. Root cause of the missing worker traces themselves is
still unconfirmed: `LD_PRELOAD` does propagate through `multiprocessing.spawn` (env is
inherited on re-exec), but no per-worker trace file appeared regardless — either the preload
lib's constructor isn't firing in the spawned child, or its output path collides with the
parent's `DFTRACER_LOG_FILE`. Needs further investigation (e.g. `strace` on a live worker PID,
or checking gotcha/brahma init-time logging) before this approach can be trusted; the win
itself (measured from the app's own epoch-timer log, independent of tracing) still stands, but
its overlap mechanism remains unvalidated.

**Separate real bug found+fixed en route:** `libdftracer_preload.so` is RPATH-linked to the
OLD system `/usr/lib64/libstdc++.so.6` (lacks `GLIBCXX_3.4.32`). Setting
`LD_PRELOAD=.../libdftracer_preload.so` alone breaks `torch_scatter`/`torch_sparse` imports for
every rank (`OSError: GLIBCXX_3.4.32 not found`) because the old libstdc++ shadows the correct
one the rest of the venv stack needs. **Fix:** prepend a modern libstdc++.so.6 ahead of it in
`LD_PRELOAD`: `LD_PRELOAD=/collab/usr/gapps/python/toss_4_x86_64_ib/anaconda3-2023.09/lib/libstdc++.so.6:<path>/libdftracer_preload.so`.
Neither `/opt/cray/pe/cce/20.0.0/...` (no libstdc++ present there) nor `gcc/12.2.0`
(`GLIBCXX_3.4.30` only, still too old) satisfies the requirement — the anaconda copy (which
Python already resolves by default without LD_PRELOAD interference, confirmed via `ldd` on
`torch_sparse`'s extension `.so`) is the one that works. Relevant any time `LD_PRELOAD` is used
with this session's torch/PyG stack, not just for this dftracer investigation.

## ROOT CAUSE FOUND AND FIXED (2026-07-22): 0-byte DataLoader worker trace files — missing per-worker dftracer finalize()

The 32 empty (0-byte) `.pfw.gz` files seen in every prior run (baseline5, io_opt3, identically)
are explained: **DataLoader worker processes (`multiprocessing_context="spawn"`,
`persistent_workers=True`) never called dftracer's `finalize()`.** Only `main_app.py`'s single
main process called `init_dftracer()`/`finalize_dftracer()` (once, at the very start/end of the
whole script). Each spawned worker gets its own dftracer C-core state (lazily started on first
`dft_event_logging(...)` call inside `Dataset_PDB.__getitem__`), but when Python's
multiprocessing tears the worker down at interpreter exit, no app-level cleanup runs — so the
worker's gzip trace stream, opened but never explicitly flushed/closed, can end up literally
0 bytes on disk (for a worker whose small event volume never happened to hit an internal
buffer-size auto-flush) or silently missing its tail events (for a busier worker that did get
occasional periodic auto-flushes, which is *also* suspect data, not confirmed-complete).

**Fix applied** (`pecan/trainer.py`): added a plain TOP-LEVEL function `_dftracer_worker_init`
(NOT a closure/nested function — see pitfall below) wired as `worker_init_fn` on both the
`train_dataloader` and `val_dataloader` constructions. It calls `init_dftracer()` +
`atexit.register(finalize_dftracer, logger)` inside every worker process, so each worker
flushes its own trace on exit. Any existing per-worker init (e.g. DYAD's `dataset.worker_init`)
is looked up dynamically via `torch.utils.data.get_worker_info().dataset` inside the function
body, rather than closed over — this is what keeps the function picklable (see below).

**Pitfall hit and fixed en route:** the first implementation used a closure-returning factory
(`_make_dftracer_worker_init(inner_fn) -> def _worker_init(worker_id): ...`) so the DYAD
worker_init could be captured — this CRASHES every rank with `AttributeError: Can't pickle
local object '_make_dftracer_worker_init.<locals>._worker_init'` the moment
`multiprocessing_context="spawn"` tries to hand `worker_init_fn` to the child process (spawn
pickles the callable; fork does not, so this bug is invisible on the default fork context —
another reason the earlier PECAN_NUM_WORKERS spawn-context fix and this fix interact). **General
lesson: any `worker_init_fn` (or other callable crossing a `spawn` process boundary) MUST be a
plain top-level function, never a closure returned by a factory** — if you need to chain
per-worker behavior that depends on runtime state (like whether DYAD staging is active), look
it up inside the function body via `get_worker_info()` instead of capturing it in an enclosing
scope.

**Validated (2026-07-22, `io_opt4_finalize`, same 4N×4GPU DDP baseline5 scale):** trace file
empty-count dropped from 32/80 to **0/48** (the previously-empty placeholder files no longer
even get created) — all 48 remaining files contain real event data, matching `epoch-2 wall time
29.72s` (statistically identical to `io_opt3`'s pre-fix 29.68s — the fix has no measurable
performance cost). One new, apparently-benign side effect observed: `HIP Intercept context
start failed: status, 2` now prints once per worker process (new vs. every prior run's log,
which had zero occurrences) — the explicit `init_dftracer()` call inside each worker now
triggers dftracer's HIP/ROCm interception layer to attempt attaching a context even in
CPU-only DataLoader workers that never touch the GPU; the status-2 failure is that attempted
attach finding no HIP context to intercept. Training proceeded normally with no crashes or
missing POSIX/HDF5 events in this validation run, so treat as benign, but it hasn't been
stress-tested at longer/larger scale — watch for it if adopting this fix elsewhere.

**Implication for the earlier "0% overlap" finding:** this does NOT fully explain the exact-0%
compute/data overlap result from the prior overlap analysis (that was measured against traces
from the 32-real/32-empty pattern, i.e. against data that — per this finding — may have been
silently truncated/incomplete even in the "populated" files due to the same missing-finalize
root cause). **The overlap analysis should be re-run against a trace collected with this fix in
place** before trusting either the original "0% overlap, real bottleneck" claim or the
follow-up "0% is a tracing artifact" hedge — both were working from data now known to be
collected without guaranteed-complete per-process flushing.

## Code gotcha (2026-07-22): `torch.FloatTensor(list)` vs `torch.stack(list)` are NOT shape-equivalent for `(1,)`-shaped per-sample tensors

`torch.FloatTensor([t1, t2, ...])` where each `t_i` has `numel()==1` silently flattens each
element to a python scalar via `__float__()`, producing a `(B,)` result regardless of the
original per-element shape. `torch.stack([t1, t2, ...])` preserves the original shape,
producing `(B, *t_i.shape)` — e.g. `(B, 1)` for `(1,)`-shaped elements. When replacing the
former with the latter to fix a device-residency bug (see the loss-block host-device sync fix
in `pecan/trainer.py`, `dftracer-compute-optimization` skill), add `.view(())` on each element
before stacking to reproduce the exact old `(B,)` shape, or the loss function's shape-broadcast
check will fail downstream — hit as a live `BCELoss` "target size different from input size"
crash the first validation-run attempt, not caught by a synthetic correctness check unless that
check specifically uses matching `(1,)`-shaped per-sample tensors.
