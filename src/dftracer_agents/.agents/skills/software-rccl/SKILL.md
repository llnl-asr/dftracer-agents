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

## The plugin DOES work — just never set `NCCL_NET` (MEASURED: 43% win)

**This supersedes earlier guidance in this skill that called the site plugin
"ABI-incompatible" and told you not to use it. That was wrong.**

The site `aws-ofi-rccl` plugin registers itself under the name **`AWS Libfabric`**. Setting
`NCCL_NET=libfabric` therefore never matches, and RCCL fails hard with
`ncclInvalidUsage ... Error: network libfabric not found` — which is easily misread as an ABI
mismatch. It is not: the plugin built against ROCm 6.4.1 loads cleanly against the RCCL 2.22.3
bundled in a torch wheel, and the site ROCm 6.4.3 ships that *same* RCCL 2.22.3.

**The fix is one line — add the plugin dir to `LD_LIBRARY_PATH` and leave `NCCL_NET` UNSET:**
```bash
export LD_LIBRARY_PATH="/collab/usr/global/tools/rccl/toss_4_x86_64_ib_cray/rocm-<ver>/install/lib:$LD_LIBRARY_PATH"
# Do NOT set NCCL_NET / NCCL_NET_PLUGIN — auto-detection finds it.
```
Confirm in the log:
```
NET/Plugin: Loaded net plugin AWS Libfabric (v5)
Using network AWS Libfabric          <- NOT "Using network Socket"
```

**Measured impact (BERT-Base 110M, 16 ranks / 4 nodes, 400 iters, replicate-confirmed):**
`train_step` **177.92s -> 100.78s = -43.4%**; wall 245s -> 152.5s. That is ~9x the measured
~5% run-to-run noise band on this system. Without the plugin RCCL silently uses host TCP at
~1.6 GB/s.

**Always verify the transport** with `NCCL_DEBUG=INFO NCCL_DEBUG_SUBSYS=INIT,NET` before
drawing ANY communication conclusion — the socket fallback is silent and costs ~44% of
training time. Channel/buffer/algorithm tuning is inert while it is in effect.

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

## FIRST: confirm which transport RCCL actually selected

Run one short job with `NCCL_DEBUG=INFO NCCL_DEBUG_SUBSYS=INIT,NET,ENV` and read the
`Using network` line **before** proposing any RCCL tuning. Channel, buffer, algorithm and
protocol levers are all **structurally inert** on a socket fallback path.

**Measured failure on Tuolumne MI300A:**
```
NET/Plugin: Could not find: librccl-net.so. Using internal network plugin.
NET/IB : No device found.
NET/Socket : Using [0]enp129s0:...<0> [1]hsi0:...<0>
Using network Socket
```
RCCL never touched Slingshot/CXI RDMA — every gradient went over **host TCP** at
**~1.6 GB/s** (220 MB fp16 grads -> ~412 MB/rank ring traffic in 255 ms), roughly 10-20x below
Slingshot CXI. On a BERT-Base 16-rank run that made `bwd_allreduce` ~104s of a 239.6s wall
(~43%).

**Cause:** the site's `aws-ofi-rccl` plugin build is compiled against a different RCCL than the
one bundled inside the torch wheel, so the plugin is not loadable and RCCL silently falls back.
**Fix:** build `aws-ofi-rccl` from source against BOTH the site ROCm and the exact RCCL inside
the wheel, expose it as `librccl-net.so`. This belongs in the BUILD step, not mid-optimization.

**Consequence for interpreting prior results:** a `gloo` vs `nccl` comparison on a socket-
fallback stack is a null *by construction* — both push bytes over host TCP. Do not read such a
null as "the workload is compute-bound and transport does not matter."

## Never pipe `module load` — it discards the module's env

```bash
module load ... rccl/working-env ... 2>&1 | grep -E "(loading|Lmod)" | head -5   # BROKEN
```
The pipeline runs Lmod in a **subshell**, so every `setenv` it performs is discarded. In a real
session this silently dropped `NCCL_SOCKET_IFNAME=hsi` and `FI_MR_CACHE_MONITOR=userfaultfd`
for **every run**, while explicitly-exported vars from the same wrapper appeared normally —
making it look like the module had been applied.

Redirect to a file or capture to a variable instead:
```bash
module load ... > /tmp/modload.log 2>&1 ; tail -5 /tmp/modload.log
```
Verify with `env | grep -E "NCCL_|FI_"` after sourcing, not by trusting the module name.

## Related

[[software-rocm]] for the ROCm/torch prerequisites, [[software-megatron-deepspeed]] for the
DDP launcher and rank-wiring pitfalls that produce RCCL-shaped symptoms.
