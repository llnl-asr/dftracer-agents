---
name: software-megatron-deepspeed-torchvision-abi
description: PyPI torchvision wheels are not ABI-compatible with a custom ROCm torch build — patch the unconditional vision import instead
metadata:
  type: feedback
---

`megatron/training.py` unconditionally imports the vision submodule chain (`knn_monitor` → `vit_dataset` → `torchvision`) even for a plain GPT pretrain run that never touches vision code paths.

**Why:** PyPI `torchvision` wheels are built against a stock CUDA/CPU torch ABI. Installing one on top of a custom ROCm torch build (e.g. `torch==2.9.1+rocm6.4`) imports without error but fails at first use: `RuntimeError: operator torchvision::nms does not exist` — its C++ extension ops never registered against this torch build.

**How to apply:** Do NOT `pip install torchvision` as a quick fix for a missing-module error on a ROCm torch build — it silently produces a broken, half-installed package. Either (a) build torchvision from source against the exact ROCm torch (matching `PYTORCH_ROCM_ARCH`), or (b) make the vision import lazy/optional in `megatron/training.py` / `knn_monitor.py` when the run doesn't use vision features (GPT/BERT text pretraining never does). Prefer (b) for text-only workloads — it's a one-line deferred-import fix vs. a multi-minute source build for a dependency that's never exercised. See [[software-megatron-deepspeed-rocm-apex]], [[project-megatron-deepspeed-gpt-pipeline]].
