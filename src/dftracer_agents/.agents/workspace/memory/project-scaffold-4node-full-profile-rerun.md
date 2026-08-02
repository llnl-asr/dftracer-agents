---
name: project-scaffold-4node-full-profile-rerun
description: ScaFFold base_960 baseline rerun at 4N x 4GPU (16 ranks) with dftracer/pydftracer@develop, HIP tracing ON and PyTorch profiler in the .pfw — COMPLETE, 960/960 epochs, 47.5M events, all categories validated
metadata:
  type: project
---

**Canonical home:** see the `workload-scaffold` skill ("4-node x 4-GPU full-profile
rerun" section — this file's full content is now persisted there) and `software-rocm`.

COMPLETE (2026-07-29). Rerun of the ScaFFold session's most-correct baseline (`base_960`:
problem_scale=6, workers=0, checkpoint_interval=1, 960 epochs, config byte-identical) at
**4 nodes x 4 GPUs = 16 ranks** (was 32), on the latest develop stack, for a complete profile.
Run dir `base16_960/`, wall **28m17s**, train timer 1560.6 s, 960/960 epochs, exit CD.

**Stack (upgraded in place in the session venv, same module env as install_stack.sh):**
dftracer `2.1.0.dev16`, pydftracer `2.0.4.dev3`, dftracer-utils `0.0.12.post1`, against
torch `2.10.0+rocm710`, python/3.11.5, cce/21.0.0, cray-mpich/9.1.0, rocm/7.1.1.
ROCm 7.1.1 is correct and has NO patched (`leakfix`/`cgroupfix`/`hangfix`) variant — the
torch wheel is pinned to `+rocm710`. See [[software-rocm]].

**What it took to get a complete profile:**
- `DFTRACER_ENABLE_HIP_TRACING=ON` must be exported explicitly; ROCm on `CMAKE_PREFIX_PATH`
  alone silently ships a build with no HIP interception. See [[dftracer-install]].
- `pydftracer` is a separate repo and must be installed from its own `@develop`.
- ScaFFold's built-in `PROFILE_TORCH` hook was extended: a `torch.profiler.schedule`
  (wait5/warmup2/active10/repeat3) stepped once per epoch via a new `perf_step()` in
  `trainer.train()`, and `on_trace_ready=dftracer.python.torch.trace_handler` so events land
  in the same `.pfw` as `cat="PP"`. See [[workload-scaffold]].
- The venv held a non-editable COPY of ScaFFold, so source edits needed a reinstall.
- The Lustre data root had been auto-deleted; recreating the 3 dirs sufficed (Phase A
  regenerates fractals in ~1 min).

**Final validated categories** (32 files, 181 pid/tid, 0 truncated, 47,523,059 events):
`STDIO` 26,308,954 · `POSIX` 10,538,370 · `PP` 8,081,570 (incl. `hipLaunchKernel`) ·
`data_loading` 1,692,528 · `dice_score` 614,528 · `trainer` 123,024 · `dftracer` 99,426 ·
`checkpointing` 31,776 · `distributed` 31,296 · `comm` 768 / `p2p` 512 / `collective` 224
(MPI) · `KFD_EVENT_UNMAP_FROM_GPU` 16 · plus instance/get_dataset/worker/losses/config_utils.

**Two structural gaps, both documented, neither a build error:**
1. Only the KFD kernel-driver HIP path fires under MPI/DDP — no `HIP_RUNTIME_API` /
   `KERNEL_DISPATCH` / `MEMORY_COPY` / `SCRATCH_MEMORY`. Reproduced on this newer dftracer +
   ROCm; GPU-kernel visibility comes from `PP` instead. See [[system-tuolumne]].
2. The `dftracer_service` node-counter daemon CANNOT run alongside this app: `torchrun-hpc`
   forces `--exclusive` on El-Capitan-family systems, so a daemon holding 1 core/node makes
   the training job unschedulable forever. Documented exception to pipeline rule 12. Also
   `dftracer_service start` under `flux run` never returns — use `flux submit`
   ([[bug-dftracer-service-start-blocks-flux-run]]).

**Sizing:** steady state ~1.7 s/epoch wall (~0.7 s train + per-epoch 256 MB/rank
checkpointing); the profiler's 51-epoch active window costs ~8 s/epoch while open, so early
progress badly under-predicts total time. 960 epochs fits a 60-min pdebug window. pbatch was
congested (7 free nodes) while pdebug had 28 — check both queues before waiting.

**How to apply:** `scripts/run_profiled.sh <run_name> <config.yml>` (4N x 4GPU, FUNCTION
mode, DATA_DIR=all, PROFILE_TORCH + DFTRACER_TORCH_PROFILE, service daemon opt-in via
`DFTRACER_SERVICE=1`); batch via `scripts/batch_base16_960.sh`. Verify after any reinstall
with `scripts/verify_stack.sh` (tolerates the known benign exit-time SIGABRT 134 from the
libomp+MKL clash).
