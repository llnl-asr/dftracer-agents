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
