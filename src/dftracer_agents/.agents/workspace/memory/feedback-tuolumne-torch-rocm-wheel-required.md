---
name: feedback-tuolumne-torch-rocm-wheel-required
description: pip install torch on Tuolumne MI300A resolves to a CUDA wheel by default — must use the ROCm wheel index explicitly
metadata:
  type: feedback
---

Plain `pip install torch` on Tuolumne (AMD MI300A) silently resolves to a CUDA wheel (`torch.version.cuda` set, `torch.version.hip=None`). `torch.cuda.is_available()` then reports `False` on the AMD GPU — this is not an NVML/driver issue (NVML doesn't apply to AMD at all), it's simply the wrong build.

**Why:** This causes DeepSpeed's `get_accelerator()` to fall back to `CPU_Accelerator`, which in turn makes Megatron-style code take the bare `torch.nn.LayerNorm` fallback path instead of apex's fused `MixedFusedLayerNorm` — and the fallback path can pass kwargs (e.g. `sequence_parallel=`) that plain `torch.nn.LayerNorm` doesn't accept, producing a `TypeError` deep in model construction that looks like an annotation/build bug but is actually accelerator misdetection.

**How to apply:** Install torch from the ROCm wheel index for this system (`pip install torch --index-url https://download.pytorch.org/whl/rocm<ver>`), then verify with `torch.version.hip is not None` and `torch.cuda.is_available()` (HIP is exposed via the `cuda` namespace) BEFORE running any smoke test or training job — don't wait to discover this deep in a crash. See [[feedback-cray-ld-library-path-fortran-runtime]], [[project-megatron-deepspeed-gpt-pipeline]].
