---
name: software-megatron-deepspeed-rocm-apex
description: ROCm apex builds on the login node (no allocation needed) but only produces the Python fallback layer, not fused HIP kernels
metadata:
  type: feedback
---

ROCm apex (github.com/ROCm/apex) can be built directly on the login node without a Flux allocation — it's a plain torch `cpp_extension` HIP build, no running job needed.

**Why:** Megatron-DeepSpeed's `megatron/model/fused_layer_norm.py` unconditionally imports `apex.normalization.fused_layer_norm.FusedLayerNormAffineFunction` at module scope with no bypass flag — this is a hard dependency for any GPU run, CPU-fallback or not.

**How to apply:** Requires `PATH` to include `$ROCM_HOME/bin` (hipcc is NOT there by default even with `ROCM_HOME` exported), `PYTORCH_ROCM_ARCH=gfx942` for MI300A, then `pip install -v --no-build-isolation .` from a shallow clone. Verify with `from apex.normalization.fused_layer_norm import FusedLayerNormAffineFunction`.

**Caveat:** This build silently produces a working Python-only apex (the import succeeds) but does NOT compile the actual HIP fused kernels (`Install Ops={...: False}` in the build log — fused_layer_norm_cuda, fused_adam_cuda etc. all False). Code paths expecting real fused kernels fall back to slower native ops rather than failing. Flag this for the optimizer/compute-dimension stage if fused-kernel performance is being evaluated. See [[feedback-tuolumne-rocm-home-deepspeed]], [[project-megatron-deepspeed-gpt-pipeline]].
