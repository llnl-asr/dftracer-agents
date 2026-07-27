---
name: software-rocm
description: ROCm environment configuration for PyTorch/DL workloads on AMD GPUs (MI300A/gfx942) — getting the right torch wheel, ROCM_HOME/ROCM_PATH, rocminfo on PATH, and pinning PYTORCH_ROCM_ARCH. Load this skill before building or running ANY PyTorch/DeepSpeed/JAX workload on an AMD-GPU system.
---

# software-rocm

Verified on an AMD MI300A (gfx942) APU cluster with Cray PE, ROCm 6.4.3, torch 2.9.1+rocm6.4,
Python 3.13. Every item below caused a real, hours-long failure before being root-caused.

## Canonical ROCm environment block

Put this in the SAME script that execs python (module/env state does not persist across
separate shell invocations):

```bash
module load <compiler> <mpi> rocm/<ver> rccl/<working-env> python/<ver>
export ROCM_HOME=/opt/rocm-<ver>
export ROCM_PATH=/opt/rocm-<ver>
export PATH="/opt/rocm-<ver>/bin:${PATH}"        # rocminfo MUST be findable — see below
export PYTORCH_ROCM_ARCH=gfx942                  # MI300A; pin it, do not autodetect
export LD_LIBRARY_PATH="/opt/rocm-<ver>/lib:${LD_LIBRARY_PATH}"
source <venv>/bin/activate
export LD_LIBRARY_PATH="<venv>/lib/python<X.Y>/site-packages/torch/lib:${LD_LIBRARY_PATH}"
```

## `pip install torch` silently installs a CUDA wheel on an AMD system

**Symptom:** deep in model construction, a confusing error such as
`TypeError: LayerNorm.__init__() got an unexpected keyword argument 'sequence_parallel'`,
or DeepSpeed falling back to `CPU_Accelerator`, or `torch.cuda.is_available()` returning
`False` on a node that definitely has GPUs. NVML warnings in the log are a red herring —
NVML does not apply to AMD at all.

**Root cause:** plain `pip install torch` resolves to the default (CUDA) wheel. It installs
and imports fine; `torch.version.cuda` is set and `torch.version.hip` is `None`. Frameworks
then correctly conclude there is no GPU accelerator and silently take CPU fallback paths,
which is where the misleading downstream errors come from.

**Fix:** install from the ROCm wheel index explicitly, matching the site's ROCm minor version
as closely as a published wheel allows:
```bash
pip install torch --index-url https://download.pytorch.org/whl/rocm<ver>
```
**Verify before doing anything else** — this is a 5-second check that saves hours:
```bash
python -c "import torch; print(torch.__version__, torch.version.hip, torch.cuda.is_available())"
# torch.version.hip MUST be non-None. On ROCm, HIP is exposed through the `cuda` namespace,
# so torch.cuda.is_available()==True is correct and expected.
```

## `MissingCUDAException: CUDA_HOME does not exist` on a correct ROCm build

**Symptom:** DeepSpeed's `op_builder.is_compatible()` raises `MissingCUDAException`, even
though torch is a genuine ROCm build and reports HIP correctly.

**Root cause:** `torch.utils.cpp_extension.ROCM_HOME` is `None` because nothing in the module
stack exports `ROCM_HOME`/`ROCM_PATH`; torch's autodetection does not find the install.

**Fix:** export both `ROCM_HOME` and `ROCM_PATH` to `/opt/rocm-<ver>`, matching the version
reported by `torch.version.hip`, BEFORE importing deepspeed or anything that triggers
`op_builder`.

## Missing `rocminfo` on PATH silently invalidates the JIT cache and deadlocks multi-rank jobs

**Symptom:** an N-rank job hangs indefinitely with no output and no error, after previously
working. Logs show `/bin/sh: rocminfo: command not found` (easy to dismiss as benign) and
then nothing.

**Root cause:** apex/DeepSpeed shell out to `rocminfo` to detect the GPU arch. When it is
absent, the derived arch string changes, which changes the torch `cpp_extension` build hash,
which invalidates the warm JIT cache. All N ranks then try to compile the same extension
concurrently and **deadlock on the shared build lock**.

**Fix:** put `$ROCM_PATH/bin` on `PATH`, and additionally pin `PYTORCH_ROCM_ARCH=gfx942` so
the build hash does not depend on `rocminfo` at all. See [[software-apex-rocm]] for the
pre-warming rule that makes multi-rank JIT safe.

## torch's own libs must be on LD_LIBRARY_PATH for lazy dlopen

**Symptom:** `torch.cuda.set_device` fails with
`Error in dlopen: libcaffe2_nvrtc.so: cannot open shared object file`, even though that file
plainly exists under the venv's `torch/lib/`.

**Root cause:** torch resolves some lazily-dlopen'd shims by bare filename rather than via its
own `$ORIGIN` RPATH. (A tracing/interposition layer such as dftracer's gotcha hooks on
`dlopen` can also defeat the RPATH lookup.)

**Fix:** `export LD_LIBRARY_PATH="<venv>/lib/python<X.Y>/site-packages/torch/lib:$LD_LIBRARY_PATH"`.

## PyPI wheels of torch-adjacent C-extension packages are NOT ABI-compatible with a custom ROCm torch

**Symptom:** the package imports, then fails at first use, e.g.
`RuntimeError: operator torchvision::nms does not exist`.

**Root cause:** the manylinux wheel's C++ extensions were built against a stock torch ABI; its
ops never register against your ROCm build.

**Fix:** do NOT `pip install torchvision`/similar as a quick fix for a missing-module error —
it produces a silently broken half-install that misleads later debugging. Either build from
source against the exact ROCm torch, or (much cheaper) make the import lazy/optional if the
workload never exercises that code path. See [[software-megatron-deepspeed]] for a worked
example of the lazy-import fix.

## Related

[[software-rccl]] for multi-GPU collectives, [[software-apex-rocm]] for fused kernels,
[[software-megatron-deepspeed]] for the app-level consequences of all of the above.
