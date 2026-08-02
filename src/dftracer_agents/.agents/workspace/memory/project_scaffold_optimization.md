---
name: project-scaffold-optimization
description: "ScaFFold (LBANN) annotation + I/O optimization session on Tuolumne, started 2026-07-08"
metadata: 
  node_type: memory
  type: project
---

**Canonical home:** see the `workload-scaffold` skill (this session's full findings,
build recipe, and measured optimizations are persisted there).

Annotation + optimization pipeline for **ScaFFold** (https://github.com/LBANN/ScaFFold.git),
LBANN's scale-free fractal deep-learning benchmark: a 3D U-Net doing semantic
segmentation on procedurally generated 3D fractal volumes. Pure Python/PyTorch
(ROCm), DDP + DistConv. Started 2026-07-08 on Tuolumne.

Session: `scaffold/20260709_064800`. MLflow parent run
`<uuid>`.

**Run conditions decided by the user:**
- Smoke test: small — `benchmark_testing.yml`, 1-2 epochs, 1 node / 1 rank.
- Baseline and every optimization iteration: **wall-time bounded to ~30 minutes**,
  NOT trained to convergence. Config written to `<WS>/configs/benchmark_opt30.yml`
  (copied from `benchmark_default.yml`, never edited in place). Same bounded
  config for baseline and all iterations so comparator diffs are apples-to-apples.
  Dice-0.95 convergence is explicitly out of scope.
- Traced runs: 8 nodes × 4 GPUs (`torchrun-hpc -N 8 -n 4 --gpus-per-proc 1`).
- `checkpoint_interval: 1` retained so per-epoch checkpoint I/O is traced — the
  prime L1/L3 target, along with the DataLoader and fractal read path.

**Why:** it is an I/O optimization study, not a training study; convergence runs
would make each optimization iteration cost hours.

**App-specific gotchas** (from `scripts/install-tuolumne.sh` and
`scripts/scaffold-tuolumne.job`):
- Launcher is `torchrun-hpc` (from hpc-launcher), not plain `flux run`.
- Install needs a patchelf pass rewriting DT_NEEDED `libmpi_gnu_112.so.12` →
  `libmpi_gnu.so.12` across `torch/lib/*.so*`.
- Run needs a specific `LD_PRELOAD` chain (libomp, libmpi_gnu, 3 MKL libs) and
  `MIOPEN_DEBUG_CONV_DIRECT_NAIVE_CONV_{FWD,BWD,WRW}=0`.
- Two-phase: `generate_fractals` (1 rank) must run before `benchmark`.
- App pins `cce/21.0.0 cray-mpich/9.1.0 rocm/7.1.1 python/3.11.5`, which differs
  from the generic `system_detect` tuolumne module set — follow the app's script.

dftracer must install into the SAME venv as ScaFFold — see
[[feedback-dftracer-aiml-venv]]. App data → Lustre, traces → `<WS>/traces/`,
see [[feedback-lustre-io]] and [[feedback-optimization-pipeline-traces]].
Profiling: [[feedback-profiling-at-session-create]].
