---
name: software-rccl
description: RCCL (ROCm Communication Collectives Library) configuration for multi-node PyTorch DDP on AMD GPUs + Cray Slingshot — which modules are load-bearing, why forcing the libfabric plugin breaks collectives, and how to validate before running a real workload. Load this skill for ANY multi-GPU/multi-node collective work on AMD hardware.
---

# software-rccl

Verified on an AMD MI300A (gfx942) cluster with Cray PE, Slingshot/Cassini NICs, ROCm 6.4.3,
torch 2.9.1+rocm6.4 (bundled RCCL 2.22.3). RCCL is NCCL-API-compatible, so PyTorch's `nccl`
backend and every `NCCL_*` env var apply to it.

## Working configuration (validated by a passing 4-node all-reduce)

```bash
module load <compiler> <mpi> rocm/<ver> rccl/<working-env-module> python/<ver>
export LD_LIBRARY_PATH="/opt/rocm-<ver>/lib:<cray mpi gnu lib>:<cce libs>:/usr/lib64:$LD_LIBRARY_PATH"
export LD_LIBRARY_PATH="$CRAY_LD_LIBRARY_PATH:$LD_LIBRARY_PATH"
export LD_LIBRARY_PATH="<venv>/lib/python<X.Y>/site-packages/torch/lib:$LD_LIBRARY_PATH"
# Do NOT set NCCL_NET / NCCL_NET_PLUGIN here — see below.
```

The site's `rccl/<working-env>` module supplies the two settings RCCL actually requires:
`FI_MR_CACHE_MONITOR=userfaultfd` (without it: racy hangs) and `NCCL_SOCKET_IFNAME=<hsi>`.

## ALWAYS validate with a standalone all-reduce before running the real workload

A single-rank success proves **nothing** about collective init. This ~30-second test is the
single highest-value check in this skill:

```python
import os, torch, torch.distributed as dist
r=int(os.environ["RANK"]); ws=int(os.environ["WORLD_SIZE"])
torch.cuda.set_device(0)
dist.init_process_group(backend="nccl", rank=r, world_size=ws)
t=torch.ones(4, device="cuda")*r
dist.all_reduce(t)
print(f"[{r}] OK -> {t.tolist()} (expect {sum(range(ws))})")
dist.destroy_process_group()
```
Launch it at the real node/rank count. If this fails, no amount of app-level debugging will help.

## The `rocm` and `rccl` MODULES are load-bearing — a torch wheel's bundled ROCm is not enough

**Symptom:** rank 0 dies silently inside RCCL's `broadcastUniqueNCCLID`, and every *other*
rank reports
`DistBackendError: [N] is setting up NCCL communicator and retrieving ncclUniqueId from [0]
... store->get('0') got error: Failed to recv, got 0 bytes. Connection was likely closed.`

This error text blames rank 0's network and is **actively misleading** — it sends you chasing
TCPStore/MASTER_ADDR/firewall problems. The real failure is that rank 0 aborted inside RCCL.

**Root cause:** relying only on the PyPI torch wheel's bundled ROCm, without loading the site's
`rocm` and `rccl` modules.

**Fix:** load both modules. Confirm with the all-reduce test above.

## Do NOT force `NCCL_NET=libfabric` / `NCCL_NET_PLUGIN=librccl-net.so` blindly

**Symptom:** collective init succeeds, then the first `all_reduce` fails with
`ncclInvalidUsage ... Last error: Error: network libfabric not found.`

**Root cause:** the site's `aws-ofi-rccl` plugin build is compiled against a specific RCCL
version. A torch wheel ships its **own** bundled RCCL (e.g. 2.22.3), and a plugin built for a
different ROCm/RCCL version is not loadable by it. Forcing `NCCL_NET=libfabric` then makes the
collective fail hard rather than fall back.

**Fix:** leave RCCL on its default network selection — collectives work. Only set `NCCL_NET`
after verifying the plugin is ABI-matched to the RCCL actually in use
(`python -c "import torch; print(torch.cuda.nccl.version())"`).

**Important corollary:** a working `launch.sh` copied from another project on the same machine
may set these vars — those settings are matched to *that* project's ROCm/RCCL versions. Do not
copy such env blocks verbatim into a different stack.

## `gloo` is a silent performance trap on GPU clusters

**Symptom:** none. Everything works; throughput is merely unremarkable.

**Root cause:** `gloo` is a valid CPU/TCP backend that never errors, so a `--distributed-backend
gloo` workaround (often introduced while debugging an RCCL rendezvous failure) survives long
after the real bug is fixed. Gradients are then staged through host memory over TCP sockets
instead of xGMI/Slingshot.

**Fix:** grep the run log for `distributed_backend` as a first-order check on any DDP job. Once
RCCL is verified working, revert to `nccl`.

**Measured reality check (do not over-promise this fix):** on a 350M-parameter model with
gradient accumulation 4 over 16 ranks, switching gloo→RCCL measured **+1.2% — statistically
no change**, against a trace-derived prediction of ~35%. At that model size the gradient
all-reduce is simply not a material fraction of iteration time. The fix is still correct, but
size the expected benefit to the actual collective volume before promising a win.

## Related

[[software-rocm]] for the ROCm/torch prerequisites, [[software-megatron-deepspeed]] for the
DDP launcher and rank-wiring pitfalls that produce RCCL-shaped symptoms.
