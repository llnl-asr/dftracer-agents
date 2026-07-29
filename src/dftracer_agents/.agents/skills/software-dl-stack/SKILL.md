---
name: software-dl-stack
description: Reusable, ordered methodology for installing a GPU deep-learning stack (PyTorch + accelerator runtime + collectives + fused kernels + MPI bindings) into a session venv on an HPC system, and validating it BEFORE running a workload. Vendor-neutral (ROCm/CUDA) with a validated ROCm/Cray reference. Load this skill for ANY PyTorch/DeepSpeed/JAX/Lightning workload install.
---

# software-dl-stack

A DL stack is a **chain of ABI contracts**: accelerator runtime → torch → collectives →
fused-kernel extensions → MPI bindings → the app. Break any link and the failure surfaces
somewhere else entirely, usually as a misleading error many layers away. This skill encodes
the install ORDER and, more importantly, the VALIDATION GATE after each layer.

The single most expensive mistake is deferring validation: every layer below can install
cleanly, import cleanly, and still be wrong. Validate each gate before proceeding.

## The cardinal rule: install env == run env

Steps 1-3 must be **byte-identical** between install time and run time. C-extension packages
bake RPATH and ABI assumptions at build time from whatever was on `PATH`/`LD_LIBRARY_PATH`.
If the run env differs, the wrong shared library loads and you get undefined symbols, silent
fallbacks, or fork crashes. Keep one `env.sh` and source it from both.

## Ordered install with a validation gate after every layer

### Layer 0 — Site modules (compiler, MPI, accelerator runtime, collectives)
Load in ONE shell invocation; module state does not persist across separate tool calls.
Check for "Inactive Modules" warnings after loading — a silently deactivated module is a
future mystery failure.

**Gate:** the module list is what you expect, and `$ACCEL_PATH/bin` (e.g. `$ROCM_PATH/bin`)
is on `PATH` — vendor tools like `rocminfo`/`nvidia-smi` must be findable, because build
systems shell out to them for arch detection.

### Layer 1 — Accelerator-aware torch (NEVER the default wheel)
A plain `pip install torch` resolves to the **CUDA** wheel and will install happily on an AMD
machine. Always pass the vendor index explicitly:
```bash
pip install torch --index-url https://download.pytorch.org/whl/rocm<ver>   # ROCm
```
**Gate — do this before anything else:**
```bash
python -c "import torch; print(torch.__version__, torch.version.hip, torch.version.cuda, torch.cuda.is_available())"
```
On ROCm: `torch.version.hip` MUST be non-None. (`torch.cuda.is_available()==True` is correct
on ROCm — HIP is exposed through the `cuda` namespace.) If this gate fails, STOP — every
downstream layer will produce misleading errors.

Also export the runtime root the extension builders look for (`ROCM_HOME`/`ROCM_PATH`, or
`CUDA_HOME`) and pin the target arch (`PYTORCH_ROCM_ARCH=gfx942`) so build hashes are stable.

### Layer 2 — torch's own libs on the loader path
```bash
export LD_LIBRARY_PATH="<venv>/lib/python<X.Y>/site-packages/torch/lib:$LD_LIBRARY_PATH"
```
torch resolves some lazily-dlopen'd shims by bare filename, not via its `$ORIGIN` RPATH.
**Gate:** `python -c "import torch; torch.cuda.set_device(0)"` on a GPU node — a
`dlopen: lib*.so: cannot open shared object file` here means this layer is missing.

### Layer 3 — Collectives (RCCL/NCCL)
**Gate — the most-skipped and most-valuable check.** A single-rank success proves NOTHING
about collective init. Run a standalone multi-node all-reduce at the real rank count:
```python
import os, torch, torch.distributed as dist
r=int(os.environ["RANK"]); ws=int(os.environ["WORLD_SIZE"])
torch.cuda.set_device(0)
dist.init_process_group(backend="nccl", rank=r, world_size=ws)
t=torch.ones(4, device="cuda")*r; dist.all_reduce(t)
print(f"[{r}] OK -> {t.tolist()} (expect {sum(range(ws))})")
```
Do not tune collectives env vars speculatively; see [[software-rccl]] for which are
load-bearing and which actively break things.

### Layer 4 — Fused-kernel extensions (apex, flash-attn, custom ops)
These are `cpp_extension` builds — **CPU-side compilation, buildable on the LOGIN NODE**, no
GPU allocation required. Budget 10-20+ minutes cold.
**Gate:** import the actual compiled modules on a COMPUTE node. Do NOT infer availability
from a framework's own status table (see [[software-apex-rocm]] for a trap that produced a
confidently-wrong conclusion).
**Concurrency rule:** pre-warm single-rank with the exact final env, or give each concurrent
variant its own `TORCH_EXTENSIONS_DIR` — N ranks JIT-compiling one extension deadlock on the
shared build lock.

### Layer 5 — MPI bindings (mpi4py), if the app needs them
Vendor MPI often ships a differently-named library than the manylinux wheel expects (e.g.
wheel dlopens `libmpi.so.12`, Cray provides `libmpi_cray.so.12`). Fix with a symlink shim dir
on `LD_LIBRARY_PATH` plus the ABI selector env var, or `patchelf --replace-needed` the
binding, or build from source with `CC`/`CXX` set to the MPI wrappers.
**Gate:** `python -c "from mpi4py import MPI; print(MPI.Get_library_version())"` — confirm it
names the vendor MPI you intend.

### Layer 6 — The application and its dependencies
**Gate:** `ldd` every key `.so` and confirm each resolves to a session-local or module-provided
path, never a stray system/anaconda copy.

## Trap: a generic package install can pre-empt Layer 1 with a CUDA wheel

**Symptom:** you carefully install the vendor torch wheel, but the venv ends up with a CUDA
torch (plus a pile of `nvidia-*` packages) anyway.

**Root cause:** any generic `pip install -e <app>/` (e.g. a session-configure step, or the
app's own `setup.py`/`pyproject.toml` listing `torch` as a dependency) resolves `torch` from
the DEFAULT PyPI index. If that runs before — or after — your vendor-index install, it
silently pulls or replaces torch with the CUDA build.

**Fix:** after ANY step that pip-installs the application or its dependencies, re-assert
Layer 1's gate:
```bash
python -c "import torch; assert torch.version.hip, torch.__version__; print('rocm torch intact', torch.__version__)"
```
If it regressed, purge (`pip uninstall -y torch torchvision torchaudio` plus any `nvidia-*`
packages) and reinstall from the vendor index. Consider pinning with a constraints file or
installing the app with `--no-deps` once its deps are already satisfied.

## ABI trap: never pip-install a torch-adjacent C-extension package against a custom torch

`torchvision`, `torchaudio`, `xformers` and friends from PyPI are built against a stock torch
ABI. Against a vendor-specific torch build they import fine and then fail at first use
(`RuntimeError: operator torchvision::nms does not exist`). Options, cheapest first:
1. Make the import lazy/optional if the workload never exercises that code path (usually a
   one-line `try/except ImportError` around an unconditional import).
2. Build from source against the exact torch.
Never leave a broken half-install in place — it silently misleads all later debugging.

## Anti-patterns

- **Deferring validation to "when we run the workload."** Every gate above exists because a
  layer failed silently and surfaced as an unrelated error hours later.
- **Copying an `env.sh`/`launch.sh` verbatim from another project on the same machine.** Those
  settings are matched to *that* project's runtime versions; collectives plugins especially
  are version-locked and will hard-fail.
- **Installing a package to satisfy an ImportError without checking ABI compatibility.**
- **Treating a single-rank success as validation of a multi-rank stack.**

## Related

[[software-rocm]], [[software-rccl]], [[software-apex-rocm]], [[software-megatron-deepspeed]]
(a fully worked example), [[software-pip]], [[software-mpi]].
