---
name: software-megatron-deepspeed-jit-and-lock-pitfalls
description: apex fused-kernel JIT compile takes 500+s cold; never delete torch cpp_extension lock files to unstick a build
metadata:
  type: feedback
---

Two operational pitfalls hit while getting the first successful Megatron-DeepSpeed GPT smoke run on Tuolumne (ROCm MI300A):

1. **apex fused-kernel JIT is slow on first run.** apex fused kernels (e.g. `scaled_upper_triang_masked_softmax_cuda`) JIT-compile per-GPU-arch sequentially via torch's `cpp_extension.load()`, taking 500+ seconds for a single extension on a cold cache (across gfx908/gfx90a/gfx942 in one build). This is cached in `TORCH_EXTENSIONS_DIR` afterward and near-instant on reruns. Budget generous wall time for any cold-cache smoke/baseline run — don't assume a slow first run means something is hung or broken.

2. **Never manually delete a lock/state file inside a torch cpp_extension cache dir to "unstick" a stale build.** Deleting just the `lock` file inside `<ext_cache>/<extension_name>/` corrupts the ninja build state for that extension rather than fixing it. To force a genuinely clean rebuild, delete the WHOLE extension subdirectory instead (`find <dir> -mindepth 1 -delete`, since bare `rm -rf` may be blocked by workspace permission policy).

See [[software-megatron-deepspeed-rocm-apex]], [[project-megatron-deepspeed-gpt-pipeline]].
