---
name: workload-scaffold
description: >
  ScaFFold (LBANN PyTorch benchmark, 3D U-Net on procedurally-generated fractals).
  Python/PyTorch DDP app running on Tuolumne MI300A. Environment setup, shared venv
  pattern for AI/ML workloads, MPI4PY Cray linkage, ROCm paths.
---

Cross-references: [[system-tuolumne]] [[software-mpi]] [[dftracer-preload-run]]

---

## Shared venv for dftracer + app (2026-07-09)

**Pattern:** For Python/AI/ML workloads (PyTorch, TensorFlow, DLIO), dftracer and the application MUST share ONE venv, never separate installs.

**Why:** Importing from multiple venvs causes library ABI mismatches (different libstdc++, libc, Cray MPI versions). DDP and mpi4py in particular are sensitive to soname/version mismatches when running distributed.

**Setup:**
```bash
# Create ONE shared venv for app + dftracer + all deps
python3 -m venv $WS/venv
source $WS/venv/bin/activate

# Install app first (ScaFFold)
pip install ".[rocm]" --extra-index-url https://download.pytorch.org/whl/rocm7.1

# Then install dftracer INTO THE SAME venv
# (via pip with environment setup, never a separate install tool)
export DFTRACER_ENABLE_MPI=ON
export MPICC="/opt/cray/pe/mpich/9.1.0/ofi/crayclang/20.0/bin/mpicc"
export MPICXX="/opt/cray/pe/mpich/9.1.0/ofi/crayclang/20.0/bin/mpicxx"
export DFTRACER_ENABLE_HDF5=ON
export HDF5_ROOT=/usr
export DFTRACER_ENABLE_HIP_TRACING=ON
pip install "git+https://github.com/llnl-asr/dftracer.git@develop"

# Then install mpi4py with Cray linkage fix (see [[software-mpi]])
export MPI4PY_MPIABI=mpich
# ... extract, patchelf, install as per software-mpi lesson ...
```

All downstream steps source the same `$WS/venv/bin/activate` and import both `dftracer` and `from mpi4py import MPI` without conflicts.

**Verification:**
```bash
python -c "import dftracer; from mpi4py import MPI; import ScaFFold; import torch; print('all OK')"
```

---

## Configuration overrides for bounded runs

ScaFFold's `benchmark_default.yml` config parameters for baseline and optimization runs must be overridden to fit ~30-min wall-clock budget:

| Parameter | Default | Override for bounded run | Rationale |
| --- | --- | --- | --- |
| `epochs` | -1 (infinite, until convergence dice ≥ 0.95) | 2–5 | Convergence take hours; small epoch count bounds time |
| `problem_scale` | 7 (128³ volumes) | 7 (keep realistic) | Problem size drives I/O; reduce only as last resort |
| `checkpoint_interval` | -1 (disabled) | 1 (every epoch) | Enable to exercise checkpoint I/O and tracing |
| `n_categories` | 5 | 2–3 | Fewer fractal categories speeds generation |
| `dataset_dir` | "datasets" (relative) | "/p/lustre5/$USER/workspaces/scaffold/datasets" | Use Lustre; NFS too slow for parallel data I/O |
| `fract_base_dir` | "fractals" | "/p/lustre5/$USER/workspaces/scaffold/fractals" | Use Lustre |
| `base_run_dir` | "benchmark_runs" | "/p/lustre5/$USER/workspaces/scaffold/runs" | Use Lustre |
| `checkpoint_dir` | In base_run_dir | Same as base_run_dir | Checkpoint I/O to Lustre |

Example config override (YAML):
```yaml
epochs: 3
checkpoint_interval: 1
dataset_dir: "/p/lustre5/$USER/workspaces/scaffold/datasets"
fract_base_dir: "/p/lustre5/$USER/workspaces/scaffold/fractals"
base_run_dir: "/p/lustre5/$USER/workspaces/scaffold/runs"
# Keep problem_scale: 7 for representative I/O load
```

**Data placement rule (critical for scalability):** All app I/O (fractals, datasets, checkpoints, runs) must go to Lustre (`/p/lustre5/$USER/...`), not NFS home. NFS serializes parallel I/O and causes pathological slowdowns in PyTorch DataLoader.

---

## Baseline run shape (8 nodes × 4 GPUs)

Tuolumne MI300A per-node: 8 GPUs, 96 CPU cores.

For baseline:
```bash
torchrun-hpc -N 8 -n 4 --gpus-per-proc 1 \
  $WS/venv/bin/scaffold benchmark -c baseline_config.yml
```

Explanation:
- `-N 8`: 8 nodes
- `-n 4`: 4 MPI ranks per node (matches 4 GPUs/node)
- `--gpus-per-proc 1`: 1 GPU per rank
- Total: 8 × 4 = 32 MPI ranks × GPUs

Set `DFTRACER_LOG_FILE=$WS/baseline/traces/raw/baseline-` to write traces to session workspace (NOT Lustre).

---

## Smoke test shape (1 node × 4 GPUs)

For quick verification before a full run:
```bash
torchrun-hpc -N 1 -n 4 --gpus-per-proc 1 \
  $WS/venv/bin/scaffold benchmark -c smoke_config.yml
```

Config overrides for smoke: `epochs: 1`, `problem_scale: 5`, `n_categories: 1`.

---


## Environment (authoritative — from the app's own scripts)

Read `scripts/install-tuolumne.sh` + `scripts/scaffold-tuolumne.job`. Use for BOTH install and run:

```bash
ml load python/3.11.5                     # NOT 3.13 — the site default breaks dftracer's native ext
ml cce/21.0.0 cray-mpich/9.1.0 rocm/7.1.1 rccl/fast-env-slows-mpi
pip install -e ".[rocmwci]"               # WCI wheels: torch==2.10.0+rocm710, mpi4py==4.1.1+mpich.9.1.0
export PIP_INDEX_URL=https://<wci-index>/simple PIP_TRUSTED_HOST=<wci-host>  # a user ~/.pip/pip.conf may hide it
# patchelf torch's MPI SONAME: libmpi_gnu_112.so.12 -> libmpi_gnu.so.12
```

Build dftracer with the **GNU** MPICH wrappers
(`/opt/cray/pe/mpich/9.1.0/ofi/gnu/11.2/bin/mpicc`), matching torch/mpi4py and the
`LD_PRELOAD`. crayclang's `mpicc` links `libmpi_cray.so.12` alongside the preloaded
`libmpi_gnu.so.12` → `double free or corruption (!prev)` at exit.

`LD_LIBRARY_PATH` must also carry `<venv>/lib/python3.11/site-packages/torch/lib`, or
dftracer's gotcha `dlopen` interception breaks torch's RPATH lookup:
`RuntimeError: Error in dlopen: libcaffe2_nvrtc.so`.

### Known benign: exit-time SIGABRT (134)
ScaFFold requires preloading ROCm's LLVM `libomp.so` (torch's `libmagma` needs
`__kmpc_dispatch_deinit`) AND MKL (`libtorch_cpu` needs `cblas_gemm_f16f16f32`). MKL's
`libmkl_gnu_thread` is a second OpenMP runtime; with dftracer's gotcha loaded the process
aborts **at exit**, after all work is done and after a complete trace is flushed.
Bisected: `libomp+mkl_gnu_thread` aborts; `libomp+mpi` ok; `mpi+mkl` ok; each alone ok.
`KMP_DUPLICATE_LIB_OK`, `OMP_NUM_THREADS`, `MKL_THREADING_LAYER` do not help; moving MKL to
`LD_LIBRARY_PATH` fails (symbols need interposition); `libmkl_intel_thread` gets hijacked by
a stale Anaconda `libmkl_gnu_thread`.
=> Runner scripts must tolerate exit code **134 only**, and validate the trace instead.

## dftracer init placement
`worker.py` initializes the singleton at import. `generate_fractals.py` must NOT call
`initialize_log()` (cli.py imports both modules for either subcommand) — it uses
`dftracer.get_instance()`. Two inits => double free.

## Config sizing (distributed runs)
`volumes = n_categories * n_instances_used_per_fractal / n_fracts_per_vol`, then
`val_split`% become validation. BOTH train and val counts must exceed the rank count or the
app raises `ValueError`. For 32 ranks: `n_categories=20, n_instances_used_per_fractal=24`
→ 160 volumes → val 48 / train 112.

`benchmark` cannot synthesize the fractal instances it reads. Two phases in one job:
`flux run -N 8 -n 32 scaffold generate_fractals -c <cfg>` then
`torchrun-hpc -N 8 -n 4 --gpus-per-proc 1 scaffold benchmark -c <cfg>` with
`datagen_from_scratch: 0`. At `problem_scale=5`, `point_num=128` → instances under `.../np128/`.

Defaults `epochs: -1` and `checkpoint_interval: -1` mean infinite/disabled — always override.
8-node `pbatch` jobs sit in SCHED; `pdebug` schedules immediately.

## Measured optimization
`dataloader_num_workers: 4` (trainer.py auto-adds `persistent_workers=True`,
`prefetch_factor=2`) → **-45.7% total_train_time** at 32 ranks. Checkpoint save is already
`world_rank==0`-gated, so `async_save=1` buys nothing and regressed. `/p/lustre5` already
provisions Data-on-MDT (`lfs getstripe -d`) — verify before proposing any striping change.

## Corrected-stack baseline findings (supersede the pre-fix diagnosis)

With the broken stack (wrong python, crayclang-vs-GNU MPI), MPI events were invisible and
POSIX transfer size measured 11-15 KB, yielding a "metadata / small-I/O bound" diagnosis.
On the corrected stack that characterization does **not** hold:

- POSIX avg transfer ~171 KB mean / 57.7 KB p50 / 528 KB p90. Read+write time (40.6s)
  exceeds metadata time (13.2s) ~3:1.
- `MPI_Barrier`: 256 calls, 13.05s aggregate = 99.3% of all MPI time (~0.41s/rank).
  Newly visible only after linking dftracer against the app's GNU MPICH.
- `checkpointing`: 322 events, 36.93s aggregate. Because `torch.save` is rank-0-gated,
  the other 31 ranks block at the next collective => the barrier stall and the checkpoint
  cost are the SAME phenomenon, not two independent bottlenecks.
- `data_loading`: p50 2us but max 220ms — a straggler tail that feeds the barrier waits.
- STDIO: 166,952 events but only 1.50s aggregate. High count, negligible time. Rank by time.

`dataloader_num_workers: 4` (-45.7%) remains the top validated L1 fix. The next target is
the serialized-checkpoint -> barrier stall, not metadata.

## Optimization results (problem_scale=6, 32 ranks, FIXED 1200 epochs, corrected stack)

Baseline calibrates at **0.625 s/epoch**; a 10-min training budget is ~960 epochs.
All variants run the SAME fixed epoch count so work is held constant.

| knob | train_s | vs base | ckpt events | POSIX_s | verdict |
| --- | --- | --- | --- | --- | --- |
| baseline (workers=0, ckpt_interval=1) | 749.9 | — | 78192 | 5666.5 | — |
| `dataloader_num_workers` 0->4 | 497.5 | **-33.7%** | 78192 (same) | 801.2 | **KEEP** |
| `checkpoint_interval` 1->4 | 576.5 | -23.1% | 19692 (-75%) | 761.9 | reject: "do-less" |
| both stacked | 503.2 | -32.9% | 19692 | 804.8 | reject: no compounding |

- `dataloader_num_workers=4` is a real win: identical checkpoint work, POSIX time -86%.
- `checkpoint_interval` gains come from writing fewer checkpoints. Always check event/byte
  counts before crediting a speedup.
- They do not compound: stacking is within noise of the dataloader fix alone.
- **`MPI_Barrier` is NOT the bottleneck** at a realistic run length: 16.8 s aggregate across
  32 ranks vs 749.9 s of training (~2%). A prior 2-second run made it look like ~65% of wall
  time. Never rank bottlenecks from a run whose training phase is a few seconds.
- `torch.save` is already `world_rank==0`-gated, so `async_save=1` has nothing to offload.

Runs with dataloader workers emit 320 trace files (32 ranks x worker procs) instead of 64, and
the known exit-time SIGABRT can truncate one file's gzip stream — tolerate `EOFError` when
aggregating.


### Replicates: the baseline is unstable, the fix is stable

Normalized per-epoch cost across two independent budgets (960 and 1200 epochs, 32 ranks):

| variant | s/epoch (960ep) | s/epoch (1200ep) | spread |
| --- | --- | --- | --- |
| baseline (`dataloader_num_workers=0`) | 0.4927 | 0.6249 | **26.8%** |
| opt1 (`dataloader_num_workers=4`) | 0.4172 | 0.4146 | **0.6%** |

So the measured improvement is **15.3% to 33.7%**, depending entirely on how contended the
filesystem was during the baseline. The direction is certain; the magnitude is not a constant.

The variance itself is the finding: a synchronous, single-threaded dataloader
(`num_workers=0`) makes training time track filesystem contention, because every batch blocks
on I/O. Prefetch workers hide that variance almost completely (0.6% spread). Quote the
improvement as a RANGE against the baseline's own noise band, never as a single headline number
from one pair of runs.

## 4-node x 4-GPU full-profile rerun (base16_960, 16 ranks, COMPLETE)

Rerun of the most-correct baseline (`base_960`: problem_scale=6, workers=0,
checkpoint_interval=1, 960 epochs, config byte-identical) at **4 nodes x 4 GPUs = 16
ranks** (was 32), on the latest develop stack, specifically to get a COMPLETE profile
with HIP tracing and the PyTorch profiler both landing in the same `.pfw`. Run dir
`base16_960/`, wall **28m17s**, train timer 1560.6s, 960/960 epochs, exit code CD.

**Stack (upgrade in place in the session venv, same module env as `install_stack.sh`):**
dftracer `2.1.0.dev16`, pydftracer `2.0.4.dev3`, dftracer-utils `0.0.12.post1`, against
torch `2.10.0+rocm710`, python/3.11.5, cce/21.0.0, cray-mpich/9.1.0, rocm/7.1.1. ROCm
7.1.1 is correct and has NO patched (`leakfix`/`cgroupfix`/`hangfix`) variant — the torch
wheel is pinned to `+rocm710`. See `software-rocm` for the general base-vs-patched-tree
matching rule this reconfirms.

**What it took to get a complete profile:**
- `DFTRACER_ENABLE_HIP_TRACING=ON` must be exported explicitly — ROCm being on
  `CMAKE_PREFIX_PATH` alone silently ships a build with no HIP interception. See
  `dftracer-install`.
- `pydftracer` is a SEPARATE repo and must be installed from its own `@develop`.
- ScaFFold's built-in `PROFILE_TORCH` hook was extended: a `torch.profiler.schedule`
  (`wait=5,warmup=2,active=10,repeat=3`) stepped once per epoch via a new `perf_step()`
  in `trainer.train()`, with `on_trace_ready=dftracer.python.torch.trace_handler` so
  torch-profiler events land in the same `.pfw` under `cat="PP"`.
- The venv held a non-editable COPY of ScaFFold, so any source edit needed a reinstall.
- The Lustre data root had been auto-deleted between sessions; recreating the 3 dirs
  sufficed (Phase A regenerates fractals in ~1 min).

**Final validated categories** (32 files, 181 pid/tid, 0 truncated, 47,523,059 events):
`STDIO` 26,308,954 · `POSIX` 10,538,370 · `PP` 8,081,570 (incl. `hipLaunchKernel`) ·
`data_loading` 1,692,528 · `dice_score` 614,528 · `trainer` 123,024 · `dftracer` 99,426 ·
`checkpointing` 31,776 · `distributed` 31,296 · `comm` 768 / `p2p` 512 / `collective` 224
(MPI) · `KFD_EVENT_UNMAP_FROM_GPU` 16 · plus instance/get_dataset/worker/losses/config_utils.

**Two structural gaps, both documented, neither a build error:**
1. Only the KFD kernel-driver HIP path fires under MPI/DDP — no `HIP_RUNTIME_API` /
   `KERNEL_DISPATCH` / `MEMORY_COPY` / `SCRATCH_MEMORY`. Reproduced on this newer dftracer
   + ROCm; GPU-kernel visibility comes from `PP` instead. See `system-tuolumne`.
2. **The `dftracer_service` node-counter daemon CANNOT run alongside this app.**
   `torchrun-hpc` forces `--exclusive` on El-Capitan-family systems, so a daemon holding
   1 core/node makes the training job unschedulable forever — a documented exception to
   pipeline rule 12 for this specific workload/launcher combination. Also,
   `dftracer_service start` under `flux run` never returns at all (a separate, generic
   bug) — use `flux submit` instead, see `bug-dftracer-service-start-blocks-flux-run`.

**Sizing:** steady state ~1.7 s/epoch wall (~0.7 s train + per-epoch 256 MB/rank
checkpointing); the profiler's 51-epoch active window costs ~8 s/epoch while open, so
early progress badly under-predicts total time. 960 epochs fits a 60-min `pdebug` window.
`pbatch` was congested (7 free nodes) while `pdebug` had 28 — check both queues before
waiting.

**How to apply:** `scripts/run_profiled.sh <run_name> <config.yml>` (4N x 4GPU, FUNCTION
mode, `DATA_DIR=all`, `PROFILE_TORCH` + `DFTRACER_TORCH_PROFILE`, service daemon opt-in
via `DFTRACER_SERVICE=1`); batch via `scripts/batch_base16_960.sh`. Verify after any
reinstall with `scripts/verify_stack.sh` (tolerates the known benign exit-time SIGABRT 134
from the libomp+MKL clash documented above).

## Checkpoint path (the dominant cost after the dataloader fix)

Checkpoint is **256.4 MB, written every epoch** = 240 GB per 960-epoch run from rank 0 alone
(~496 ms/write, ~517 MB/s). `total_train_time` EXCLUDES this — opt1 is 400.5 s "train" but
1052.9 s wall. Measure wall clock.

| change | paired wall delta | verdict |
| --- | --- | --- |
| best-loss cache (stop re-reading `checkpoint_best.pth` every epoch) | -1.9% | keep |
| `async_save=1` | -4.4% cumulative | keep (saturates: 496 ms write > 415 ms epoch, pipelines 1 deep) |
| `hardlink_best=1` (os.link instead of 256 MB copyfile) | **-2.5%** | keep |
| `ckpt_stage_dir=/dev/shm` + `shutil.move` | **+6.3%** | REJECT |

- `shutil.copyfile(last, best)` fired on 102/960 epochs = 25.5 GB of duplicate writes.
  Replace with temp + `os.replace` + `os.link`. The temp+rename is REQUIRED: `torch.save`
  truncates in place, so a hardlinked `best` on a reused inode is corrupted by the next write.
- Naive `/dev/shm` staging is WORSE: tmpfs and Lustre are different devices, so `shutil.move`
  becomes read+write — the 256 MB is written twice. Multi-level checkpointing only pays when the
  PFS drain runs on a separate thread from the local write with pipeline depth > 1
  (Moody et al., SCR, SC 2010, https://doi.org/10.1109/SC.2010.18). On an MI300A APU, `/dev/shm`
  also consumes HBM shared with the GPU.
- `batch_size` is PER RANK: `batch_size * world_size` must be <= training samples
  (4 x 32 = 128 > 112 -> ValueError).
