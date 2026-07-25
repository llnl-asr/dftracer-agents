---
name: software-molformer
description: Build/annotate/run caveats for IBM MoLFormer (chemical-language transformer, PyTorch + PyTorch Lightning + apex) discovered on Tuolumne (AMD MI300A, ROCm).
---

# software-molformer

Lessons from annotating and optimizing IBM MoLFormer (github.com/IBM/molformer) with
dftracer on Tuolumne (AMD MI300A APU, ROCm 6.3.1).

## Duplicate `dftracer.initialize_log()` in imported (non-entry-point) files silently corrupts traces

**Symptom:** dftracer FUNCTION-mode tracing produces a trace file with 0 or near-0 events, or
one that looks structurally empty, with no error raised anywhere.

**Root cause:** `pubchem_encoder.py` and `dataset_pubchem.py` — both regular imported library
modules, not entry points — carried their own `dftracer.initialize_log()` (and matching
`finalize()`) calls, presumably copy-pasted from the entry-point annotation template. Because
these files are imported before the real entry point (`train_pubchem_light.py`) runs its own
`initialize_log()`, the C++ profiler singleton gets initialized (and in some import orders,
finalized) by the wrong caller, corrupting all subsequent annotated spans in the real run.

**Fix:** `initialize_log()`/`finalize()` must ONLY appear in the true entry-point script(s)
(here: `train_pubchem_light.py` and the 3 `finetune_pubchem_light*.py` files). Any imported
module that is annotated with `@dft_fn` for its own functions must NOT also call
`initialize_log()`/`finalize()` — those calls belong exactly once, at the top of the real
`if __name__ == "__main__":` entry point.

**Generically applicable:** this is not a MoLFormer-specific bug — any Python FUNCTION-mode
annotation pass that puts `initialize_log()` in more than one file (e.g. because an annotator
tool applied its "each file gets the full decorator template" logic uniformly instead of
distinguishing entry points from imports) will hit the same silent corruption. Worth checking
against the `ml_annotate`/python-annotator tool's own logic: it should only emit
`initialize_log()`/`finalize()` calls for files that are confirmed to be actual script entry
points (`if __name__ == "__main__"` present and/or named in the task's launch command), never
for every annotated file.

## ROCm/PyTorch wheel version must match the loaded ROCm module exactly

A torch wheel built for a different ROCm minor version than the currently-loaded `rocm/X.Y.Z`
module causes import-time or first-kernel-launch failures. On Tuolumne with `module load
rocm/6.3.1`, the PyTorch wheel must be the ROCm 6.3-series build, not e.g. a 6.1 or 6.4 wheel.

## `libcaffe2_nvrtc.so` needs an explicit `LD_LIBRARY_PATH` entry

Even with the correct ROCm-matched torch wheel installed, `libcaffe2_nvrtc.so` is not always
resolvable via the venv's own site-packages layout on this system; add
`${WS}/install/lib/python3.13/site-packages/torch/lib` to `LD_LIBRARY_PATH` explicitly before
running (see `final_report/scripts/run_baseline.sh` for the working pattern).

## `lightning_fabric`'s Ampere-capability check crashes on AMD GPU device names

PyTorch Lightning's `lightning_fabric` accelerator-capability probing code assumes NVIDIA-style
`torch.cuda.get_device_capability()` semantics/naming; on ROCm-backed devices with AMD GPU
names (e.g. MI300A) this probe can crash outright rather than gracefully falling back. Requires
a targeted skip/patch of the Ampere-capability check when running PyTorch Lightning on ROCm.

## apex must be built from the ROCm/apex fork, never upstream NVIDIA/apex

Upstream `NVIDIA/apex --cuda_ext` hard-requires `nvcc`/`CUDA_HOME` (calls
`/usr/local/cuda/bin/nvcc`), which does not exist on this ROCm cluster. Build
`https://github.com/ROCm/apex` instead, against the session's HIP toolchain — same class of
fix as building dftracer's own C core for HIP.

## bf16-mixed precision hurts small models; batch size is the real throughput lever

Measured on Tuolumne, 1 node x 4 MI300A GPUs, ~1M-parameter MoLFormer validation config, real
20K-molecule PubChem slice, DDP world_size=4, fixed 1-epoch/157-step work:

| Config | Throughput | vs baseline |
|---|---|---|
| baseline (batch=32/GPU, workers=2) | 536 samples/s | -- |
| bf16-mixed precision | 307 samples/s | **-43% (measured worse, rejected)** |
| num_workers=1 (vs 2) | 525 samples/s | ~neutral (noise) |
| batch=64/GPU | 970 samples/s | **+81%** |
| batch=128/GPU + workers=2 | 1638 samples/s | **+206% (best)** |

At this small model scale, GradScaler's per-step dtype-casting/loss-scaling overhead outweighs
any reduced-precision GEMM throughput gain — bf16-mixed should only be re-tested at the full
~350M-parameter production MoLFormer config, where GEMMs dominate more of the step time.
Batch-size scaling is the lever that actually amortizes Python-level per-step overhead
(dataloader iteration, autograd graph construction, kernel-launch dispatch) on this
unified-memory APU. `PYTORCH_HIP_ALLOC_CONF=expandable_segments:True` is confirmed NOT
SUPPORTED on this ROCm platform (explicit PyTorch runtime warning, silently a no-op) — the
+206% result is attributable entirely to the batch-size change, not this flag.

## Non-`--exclusive` single-task `flux run` + PyTorch Lightning DDP can fork-bomb the node

**Symptom:** launching the 4-GPU DDP training command via `flux run -N1 -n1 bash wrapper.sh`
(no `--exclusive`) produces zero stdout/trace output for tens of minutes, then hundreds of
identical `bash wrapper.sh` processes pile up on the node (verified via `ps aux` on the node —
process count kept growing, no error ever surfaced to the job's stdout/stderr).

**Root cause, two compounding issues:**
1. Flux's built-in PMI shim sets `PMI_RANK=0` / `PMI_SIZE=1` / `PALS_APID` / `PALS_NODEID` /
   `PALS_RANKID` / `PALS_SPOOL_DIR` / `LDCS_RANKINFO` in the environment for **any** `flux run`,
   even a plain single-task, non-MPI one. Every child process PyTorch Lightning's DDP
   subprocess launcher spawns inherits this SAME `PMI_RANK=0` unchanged (Flux does not rewrite
   it per child), so no child can ever tell "I am an already-spawned child" — each one
   re-evaluates "am I the parent that needs to spawn my siblings?" as true, causing infinite
   recursive self-relaunch.
2. This only actually fires when combined with a real error condition: launching
   `flux run -N1 -n1` WITHOUT `--exclusive` only grants the job a fair-share slice of the
   node's resources — here, 1 of the node's 4 GPUs — while the training command asks for
   `--gpus 4`. PyTorch Lightning's `MisconfigurationException` ("You requested gpu: [0,1,2,3]
   But your machine only has: [0]") is the trigger that sends the corrupted-env relaunch logic
   into its infinite loop instead of surfacing the exception once and exiting.

**Fix (apply both, defense-in-depth):**
- Always launch this app's DDP training with `--exclusive` on the `flux run`/`flux submit` —
  it needs the WHOLE node's 4 GPUs, not a fair-share slice. This alone fixes the actual GPU
  mismatch and is what the originally-working run scripts always did
  (`flux run -N 1 -n 1 --exclusive ...`) — dropping `--exclusive` (e.g. when a tool call
  doesn't add it, as `session_run_with_dftracer` does not by default) is the regression trigger.
- Before invoking `python`, unset the Flux/Cray PMI pollution:
  `unset PMI_RANK PMI_SIZE PMI_FD PALS_APID PALS_APINFO PALS_NODEID PALS_RANKID PALS_SPOOL_DIR LDCS_RANKINFO`
  so PyTorch Lightning's cluster-environment auto-detection falls back to its own
  `LightningEnvironment` (LOCAL_RANK-based, correctly self-terminating) instead of whatever
  MPI-like environment it infers from the PMI vars.

**If this ever recurs:** cancel the job immediately (`flux proxy <alloc> flux cancel <jobid>`)
and verify process cleanup on the node
(`flux proxy <alloc> flux run -N1 -n1 --requires=hosts:<node> ps aux | grep -c <script_name>`)
before relaunching — do not let a suspected fork bomb keep running while you investigate.

## Ray variant (`molformer_ray_descriptors.py`, Ray Train/Tune): multi-node bring-up bugs

The Ray-based variant of this app (distinct from the PyTorch-Lightning variant documented
above) hits several distinct, non-obvious multi-node bring-up bugs on Tuolumne. See also
`software-ray` for the general (not MoLFormer-specific) `ray start --head` jemalloc bug.

1. **`ray.init()` with no args silently builds an isolated single-node cluster** if
   `RAY_ADDRESS` isn't exported, even when a real multi-node cluster was already bootstrapped
   via `ray start --head` / `ray start --address=`. Symptom: the driver process stays alive at
   near-0% CPU indefinitely with `ScalingConfig(num_workers=N)` requesting more
   GPUs/workers than the accidentally-isolated 1-node cluster has, and Tune's
   `insufficient_resources_manager` warns "cluster only has ... GPUs available" every 60s
   forever. Fix: `export RAY_ADDRESS="<head_ip>:<port>"` before launching the training script.
2. `molformer_ray_descriptors.py` falls back to `DATASET_CSV_PATH = script_dir/../dataset/
   baseline/pubchem_filtered.csv` when the `DATASET_CSV_PATH` env var isn't set — this relative
   path does not exist under `annotated/src/../dataset` (the real dataset lives at
   `<WS>/dataset/baseline/`). Always export `DATASET_CSV_PATH` explicitly.
3. When bootstrapping head+worker manually with `ray start --head` (not the head's default
   auto-detected GPU count), do NOT pass `--num-gpus=0` to the head unless the head is
   genuinely a non-GPU control-plane node — on Tuolumne the "head" is a real MI300A GPU node
   like every other allocated node, and `ScalingConfig(num_workers=args.nodes*4)` expects
   4 GPUs contributed by EACH physical node, not just the workers.
4. A worker script that `sleep`s a fixed duration before `ray stop` (to keep its GPUs alive
   while the head trains) is a race: if actual training takes longer than the sleep, the
   worker drops out from under the head mid-run. Use a completion-marker file the head
   `touch`es on exit, polled by the worker, instead of a fixed sleep.
5. **Unresolved as of 2026-07-25:** even after fixes 1-4, the run reliably reaches "Connected
   to Ray cluster" + dataset path resolution, then stalls indefinitely (near-0% CPU, no
   traceback, no Tune scheduling warning, no `ray::` worker actor ever appears) before any
   dftracer I/O trace data is produced. `py-spy` was not available in the session venv to get
   a stack trace of the stalled process — installing it (`pip install py-spy`, needs to be
   pre-cached since compute nodes have no internet) and running `py-spy dump --pid <pid>` on
   the stalled driver is the recommended next diagnostic step. Leading suspects: (a)
   `AutoTokenizer`/`AutoConfig.from_pretrained(..., trust_remote_code=True)` still attempting a
   network round-trip to huggingface.co despite `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1`;
   (b) a Ray placement-group/actor-scheduling deadlock tied to the MI300A `accelerator_type`
   resource label; (c) a silently OOM-killed worker actor.
