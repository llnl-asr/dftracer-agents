---
name: feedback-tuolumne-rocm-home-deepspeed
description: DeepSpeed op_builder raises MissingCUDAException on a correct ROCm torch build because ROCM_HOME/ROCM_PATH isn't auto-detected on Tuolumne
metadata:
  type: feedback
---

Even with a correctly-installed ROCm torch build (`torch.version.hip` set, `torch.cuda.is_available()==True`), DeepSpeed's `op_builder.is_compatible()` can raise `MissingCUDAException: CUDA_HOME does not exist` because `torch.utils.cpp_extension.ROCM_HOME` is not auto-detected on Tuolumne.

**Why:** Nothing in the standard module load sequence exports `ROCM_HOME`/`ROCM_PATH`, so torch's own autodetection fails even though the torch build itself is ROCm-correct.

**How to apply:** Before importing `deepspeed` (or anything that triggers `op_builder`), export `ROCM_HOME=/opt/rocm-<version>` and `ROCM_PATH=` the same path, matching the version reported by `torch.version.hip`. See [[feedback-tuolumne-torch-rocm-wheel-required]], [[project-megatron-deepspeed-gpt-pipeline]].
