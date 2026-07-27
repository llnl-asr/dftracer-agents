---
name: software-megatron-deepspeed-rocm-run-pitfalls
description: Megatron-DeepSpeed ROCm run pitfalls: vision-import lazy-guard, torch/lib LD_LIBRARY_PATH, smoke-test dataset scale
metadata:
  type: feedback
---

Getting Megatron-DeepSpeed's GPT smoke test running on a ROCm (Tuolumne MI300A) build surfaced several distinct pitfalls beyond the ROCm apex build (see [[software-megatron-deepspeed-rocm-apex]]):

1. **Unconditional vision import.** `megatron/training.py` imports `megatron.model.vision.knn_monitor.compute_feature_bank` unconditionally at module scope, which pulls in `torchvision` even for text-only GPT runs. Wrap it in `try/except ImportError` with a stub that only raises if actually called — the real call site is already gated by `args.vision_pretraining_type == "dino"`, never true for GPT/BERT text pretraining. See [[software-megatron-deepspeed-torchvision-abi]] for why installing torchvision itself is the wrong fix.

2. **torch/lib not on LD_LIBRARY_PATH for lazy dlopen.** `torch.cuda.set_device` can fail with `Error in dlopen: libcaffe2_nvrtc.so: cannot open shared object file` even though the .so exists under the venv's `torch/lib/` — torch's lazy dlopen resolves by bare filename against LD_LIBRARY_PATH, not via its own RPATH. Fix: `export LD_LIBRARY_PATH="<venv>/lib/python3.13/site-packages/torch/lib:$LD_LIBRARY_PATH"`.

3. **Smoke-test dataset must be small, not just present.** Megatron's `_build_index_mappings` runs single-rank and builds an unbounded `sample_idx.npy` sized to the full corpus (a full oscar/openwebtext corpus at seq-len 64 produced a 67GB+ growing index before a single training iteration). A smoke test must point `--data-path` at a small/truncated slice (a few thousand documents) or cap it with a pre-sliced index — verifying the path merely exists is not enough; check document/sample count before launching, especially within a time-boxed allocation.

**Why:** Each of these looks like an annotation or dftracer bug at first glance but is a genuine pre-existing app/environment issue, unrelated to instrumentation.

**How to apply:** Apply all three fixes before any GPT/BERT smoke test on this app+system combo. See [[project-megatron-deepspeed-gpt-pipeline]].
