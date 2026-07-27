---
name: software-megatron-deepspeed-compute-tuning-findings
description: Megatron-DeepSpeed on ROCm/MI300A is already compute-tuned; bf16 costs 25%, flash-attn flags silently no-op, DeepSpeed op-builder table misreads as a fused-kernel gap
metadata:
  type: feedback
---

Measured compute-dimension findings for Megatron-DeepSpeed GPT-350M on Tuolumne MI300A (ROCm 6.4, torch 2.9.1+rocm6.4, DeepSpeed 0.19.3), from a 12-run replicated study.

1. **The default config is already fully compute-tuned.** All four fusions + persist-layernorm + apex `FusedAdam` are active out of the box. Kernel fusion is NOT an available lever.

2. **Do not misread DeepSpeed's op-builder table as a missing-kernel gap.** A log line like `fused_adam ... [NO] ... [OKAY]` (or `Install Ops={...: False}`) is DeepSpeed's own op status table: `[NO]` = not pre-built, `[OKAY]` = JIT-capable. It is the NORMAL state and says nothing about apex. To actually check fused-kernel availability, probe imports on a COMPUTE node: `amp_C`, `fused_layer_norm_cuda`, `fused_weight_gradient_mlp_cuda`, `apex.optimizers.FusedAdam`. (This exact misread produced a false "fused kernels never compiled" conclusion in one session before being corrected.)

3. **`--bf16` costs ~25% throughput vs `--fp16`** (measured: +32.6% iteration time, 107.5 vs 142.5 samples/sec; effect ~15x the ~4% cross-replicate noise band). This is NOT a hardware limit — MI300A supports bf16 natively (`torch.cuda.is_bf16_supported()=True`). It is DeepSpeed 0.19.3's `BF16_Optimizer` path being far less optimized than its fused fp16 loss-scaler path. Note the loss trajectory also differs, so it is not a like-for-like numerical comparison.

4. **All four `--use-flash-attn*` flags silently no-op** on this stack: `-v1/-v2/-triton` need the `flash_attn` package (absent), and `--use-flash-attn-builder` needs DeepSpeed's `FlashAttentionBuilder`, which returns `None` on 0.19.3 + ROCm. Megatron leaves `use_flash_attn=False` WITHOUT erroring — always confirm the resolved value in the run's arg dump rather than trusting the flag. FlashAttention remains the highest-ceiling untested lever (~17% potential) but needs a multi-hour ROCm/CK build.

5. **`TORCH_BLAS_PREFER_HIPBLASLT` is inert in BOTH directions** (=1 and =0 both within noise) — torch 2.9.1 already defaults to hipBLASLt on gfx942. Testing both directions is what proves a knob is genuinely inert rather than already-optimal.

6. **MIOpen autotune is irrelevant** to GPT decoders (it tunes convolutions; there are none). NUMA/core-affinity is a twice-measured no-op on this unified-HBM APU.

**Why:** Several of these look like obvious optimizations and are not; two of them (op-builder table, silent flash-attn no-op) actively mislead by producing plausible-looking evidence for a wrong conclusion.

**How to apply:** Before proposing kernel/precision levers on this stack, dump the app's own fusion/precision/parallelism args and probe kernel imports on a compute node — a fully-tuned baseline is a common outcome that invalidates most of the proposal set. See [[software-tuolumne-pytorch-ddp-working-stack]], [[project-megatron-deepspeed-gpt-pipeline]].
