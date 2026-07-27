---
name: software-tuolumne-pytorch-ddp-working-stack
description: VERIFIED working multi-node PyTorch DDP stack on Tuolumne — rocm+rccl modules, no forced libfabric plugin, stable JIT hash
metadata:
  type: feedback
---

Multi-node PyTorch DDP (RCCL collectives) on Tuolumne MI300A. This stack is VERIFIED: a 4-node/4-rank torch all-reduce returns the correct result with exactly these settings.

```bash
module load ... cce/<ver> cray-mpich/<ver> rocm/<ver> rccl/working-env python/<ver>
export LD_LIBRARY_PATH="/opt/rocm-<ver>/lib:/opt/cray/pe/mpich/<ver>/ofi/gnu/11.2/lib:<cce libs>:/usr/lib64:$LD_LIBRARY_PATH"
export LD_LIBRARY_PATH="$CRAY_LD_LIBRARY_PATH:$LD_LIBRARY_PATH"
export LD_LIBRARY_PATH="<venv>/lib/pythonX.Y/site-packages/torch/lib:$LD_LIBRARY_PATH"
export PATH="/opt/rocm-<ver>/bin:$PATH"     # rocminfo MUST be findable
export PYTORCH_ROCM_ARCH=gfx942             # MI300A
export ROCM_HOME=/opt/rocm-<ver>; export ROCM_PATH=/opt/rocm-<ver>
```

Four distinct failures, each with a non-obvious cause:

1. **`rocm` and `rccl` MODULES are load-bearing.** Relying only on a PyPI torch wheel's bundled ROCm makes rank 0 die silently inside RCCL's `broadcastUniqueNCCLID`; every *other* rank then reports `DistBackendError: ... retrieving ncclUniqueId from [0] ... Failed to recv, got 0 bytes`, which misleadingly blames rank 0's network. `rccl/working-env` supplies the two settings RCCL actually needs (`FI_MR_CACHE_MONITOR=userfaultfd`, `NCCL_SOCKET_IFNAME=hsi`).

2. **Do NOT force `NCCL_NET=libfabric` / `NCCL_NET_PLUGIN=librccl-net.so`.** The site's aws-ofi-rccl plugin build is not loadable by the RCCL bundled in a recent torch wheel; forcing it fails the collective with `ncclInvalidUsage ... Error: network libfabric not found`. Default network selection works. (A ScaFFold-style launch.sh that sets these is matched to *its own* rocm/rccl versions — do not copy it verbatim into a different ROCm stack.)

3. **`rocminfo` missing from PATH silently breaks the JIT cache.** apex/DeepSpeed shell out to `rocminfo` for GPU-arch detection; when absent, the derived arch string and therefore the torch `cpp_extension` build hash change, invalidating the warm JIT cache. All N ranks then recompile the same extension concurrently and **deadlock on the shared build lock** — the job hangs with no output and no error. Fix: put `$ROCM_PATH/bin` on PATH and pin `PYTORCH_ROCM_ARCH`.

4. **Pre-warm the JIT and the dataset index cache single-rank** using the EXACT same env as the multi-rank run, before launching N ranks. Both caches persist on disk across allocations. Concurrent multi-rank cache *building* races (index cache -> truncated `.npy` -> `EOFError: No data left in file` from `_build_index_mappings`; JIT -> lock deadlock).

**How to apply:** Validate with a 2/4-node all-reduce smoke test BEFORE the real workload — a single-rank success proves nothing about collective init. See [[feedback-cray-ld-library-path-fortran-runtime]], [[feedback-mpi4py-cray-soname-mismatch]], [[software-megatron-deepspeed-jit-and-lock-pitfalls]], [[software-megatron-deepspeed-launcher-world-size-1]].
