---
name: software-apex-rocm
description: NVIDIA/ROCm apex (fused kernels for transformer training) on AMD GPUs — building it, verifying fused kernels are ACTUALLY active, the multi-rank JIT build-lock deadlock, and the DeepSpeed op-builder table that is routinely misread as a missing-kernel gap. Load this skill for any Megatron/transformer training stack that imports apex.
---

# software-apex-rocm

Verified on AMD MI300A (gfx942), ROCm 6.4.3, torch 2.9.1+rocm6.4, Python 3.13, DeepSpeed 0.19.3.

Note: the required package is **ROCm apex** (github.com/ROCm/apex) — NOT the unrelated PyPI
package named `apex`, which is something else entirely and will not satisfy the import.

## apex builds on the LOGIN NODE — no allocation needed

It is a plain torch `cpp_extension` HIP build: CPU-side compilation, no running GPU job
required. Do not burn a compute allocation on it.

```bash
# after the standard ROCm env block (see software-rocm)
export PATH="$ROCM_PATH/bin:$PATH"     # hipcc is NOT on PATH by default, even with ROCM_HOME set
export PYTORCH_ROCM_ARCH=gfx942
git clone --depth 1 https://github.com/ROCm/apex
cd apex && pip install -v --no-build-isolation .
```
Takes roughly 7-20 minutes. Verify:
```bash
python -c "from apex.normalization.fused_layer_norm import FusedLayerNormAffineFunction; print('ok')"
```

## Do NOT read DeepSpeed's op-builder table as an apex fused-kernel gap

**Symptom (a trap):** the run log prints something like
```
fused_adam ............. [NO] ....... [OKAY]
```
or the apex build log shows `Install Ops={'fused_adam': False, ...}`. This looks exactly like
"the fused kernels were never compiled" and will send you down a multi-hour rebuild.

**Root cause:** that table is **DeepSpeed's own op-builder status table**, where `[NO]` means
"not pre-built" and `[OKAY]` means "JIT-capable". That is the completely normal state and says
nothing whatsoever about apex.

**How to actually check** — probe the imports on a COMPUTE node:
```bash
python -c "import amp_C, fused_layer_norm_cuda, fused_weight_gradient_mlp_cuda; \
from apex.optimizers import FusedAdam; print('fused kernels active')"
```
In a real session this misread produced a confidently-stated but false "fused kernels were
never compiled" conclusion that then got used to explain an unrelated performance regression.
Probe, do not infer.

## Megatron hard-requires apex — there is no bypass flag

`megatron/model/fused_layer_norm.py` imports
`apex.normalization.fused_layer_norm.FusedLayerNormAffineFunction` **unconditionally at module
scope**. No config flag routes around it, so apex must be installed for any GPU run.

## Multi-rank JIT compilation deadlocks on the shared build lock

**Symptom:** an N-rank job hangs indefinitely, no output, no error, no traceback. The last log
lines are a kernel compile or a `rocminfo: command not found` warning.

**Root cause:** all N ranks call `cpp_extension.load()` for the same extension at once and
serialize on a shared file lock; under the wrong conditions they deadlock. Anything that
changes the build hash (a missing `rocminfo`, a different arch string, a precision change)
invalidates the warm cache and triggers this.

**Fix — two rules:**
1. **Pre-warm single-rank** with the EXACT same environment as the multi-rank run, then launch
   N ranks against the warm cache.
2. If running variants concurrently, give each its own `TORCH_EXTENSIONS_DIR` so they can never
   share a lock. (Cost: each variant pays a cold JIT compile once.)

**Never** delete a `lock` file inside a `cpp_extension` cache dir to "unstick" a build — that
corrupts the ninja state. To force a clean rebuild, delete the whole extension subdirectory.

## Cold JIT compiles are slow — budget for them

A cold apex fused-kernel build can take 500+ seconds for a single extension (it compiles per
GPU-arch sequentially). In a short benchmark run this dominates iteration 1 — always exclude
iteration 1 as warmup when computing steady-state throughput, or you will report a ~14x-wrong
number. Cached afterwards in `TORCH_EXTENSIONS_DIR`.

## Related

[[software-rocm]] (PATH/arch prerequisites), [[software-megatron-deepspeed]] (the consumer),
[[software-rccl]] (collectives).
