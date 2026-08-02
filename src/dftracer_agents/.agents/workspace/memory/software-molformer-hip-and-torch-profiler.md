---
name: software-molformer-hip-and-torch-profiler
description: IBM MoLFormer re-run on develop dftracer with HIP tracing + PyTorch profiler — all categories verified, but 4-rank DDP SIGABRTs when the torch profiler runs a long active window
metadata:
  type: project
---

**Canonical home:** see the `software-molformer` skill (app-specific findings: build
recipe, 4-rank/16-GPU DDP SIGABRT, LSF hardcode patch) and the new `software-rocm`
skill (generic findings: HIP init-ordering, profiler-cycle conflict, ROCm
base-vs-patched tree matching, RCCL transport selection).

Re-ran IBM MoLFormer (PyTorch Lightning, AMD MI300A) in the existing session
under the LATEST `develop` dftracer + pydftracer, with **HIP tracing**
(rocprofiler-sdk) and the **PyTorch profiler** both enabled.

**Build recipe that works:**
- `pip install git+.../dftracer.git@develop` with `DFTRACER_ENABLE_HIP_TRACING=ON`,
  `CC=$(which mpicc) CXX=$(which mpicxx)`, `CMAKE_PREFIX_PATH=$ROCM_PATH`.
  rocprofiler-sdk is present in the site ROCm trees (cmake config + headers +
  `librocprofiler-sdk.so`), so HIP tracing needs no extra dependency.
- `pydftracer` MUST come from `@develop` — the PyPI release has no
  `dftracer.python.torch` module. That module provides `trace_handler`, which
  you pass as torch's `on_trace_ready`; it converts torch profiler results into
  dftracer events under the **`PP`** category on the same clock.
- Two install pitfalls hit here: [[bug-dftracer-stale-brahma-after-pip-uninstall]]
  and [[bug-dftracer-crayclang-rpath-shadows-libstdcxx]].

**ROCm version choice matters.** Building dftracer against the *patched*
`rocm/<ver>hangfix` tree while torch is a `+rocm<ver>` wheel built against the
BASE tree mixes two rocprofiler/HIP runtimes and made the app abort before
training even started. Rebuilding dftracer against the SAME base ROCm tree the
torch wheel targets let training start and the profiler engage on all ranks.
This is a real qualifier on [[software-rocm]]'s "prefer the patched module"
rule: prefer patched, but dftracer and the torch wheel must agree.

**Categories VERIFIED present** (single-rank smoke test AND a 4-rank DDP run,
5.46M events): `HIP_RUNTIME_API`, `KERNEL_DISPATCH`, `MEMORY_COPY`,
`SCRATCH_MEMORY`, `PAGE_MIGRATION`, `RCCL_API` (collectives), `PP` (torch
profiler: `aten::*`, `hipLaunchKernel`, rocsolver kernels), `POSIX`, `STDIO`,
plus the app's own annotation categories. Trace metadata confirms
`build:{hip:1}` and `used:{LIBC_IO, HIP, PYTHON, torch_profiler,
python_function}` — that `used` map is the cheapest authoritative check that a
layer actually fired.

**OPEN ISSUE — 4-rank DDP SIGABRT.** With 4 ranks on one node, a rank dies with
code -6 a few minutes into training whenever the torch profiler runs a long
active window (`wait=5,warmup=2,active=10`), with no Python traceback.
Established by bisection:
- NOT caused by the torch profiler alone — restricting profiling to rank 0 only
  still killed rank 3 (a non-profiling rank), which points at peers desyncing
  while the profiling rank stalls processing its trace, not at the profiler API.
- HIP tracing alone (profiler off) ran 40+ min without aborting, but is very
  slow — rocprofiler-sdk on every HIP API call is heavy; budget well beyond the
  untraced wall time.
- A SHORT window (`wait=2,warmup=1,active=2`, rank 0 only) cleared the point
  where the long window died and was still running at 20+ min when the
  allocation expired — promising but NOT confirmed to completion.
- `rccl/working-env` is required, not optional: without it the job hangs
  instead (its `FI_MR_CACHE_MONITOR=userfaultfd` is load-bearing, per
  [[software-rocm]]'s "RCCL transport selection" section — RCCL-specific
  content now lives there, there is no separate `software-rccl` skill).

## 4-node / 16-GPU scale-out

Two things had to be fixed before MoLFormer would launch multi-node at all:
- Upstream hardcodes **LSF** (`os.environ["LSB_MCPU_HOSTS"]`) in the
  `num_nodes > 1` branch and then prints `NCCL_IB_CUDA_SUPPORT` unguarded — both
  raise `KeyError` on a Flux site. Patch: honour a pre-set
  `MASTER_ADDR`/`NODE_RANK` supplied by the launch script and `.get()` the prints.
- Launch **one flux task per node** (`-N4 -n4 -c 96 -g 4`) and let Lightning
  spawn the 4 local ranks (`--num_nodes 4 --gpus 4`). Do NOT launch one task per
  GPU: flux then hands every task the SAME physical device and RCCL dies with
  `Duplicate GPU detected : rank N and rank 0 both on CUDA device 302000`
  (`ncclInvalidUsage`). With 1 task/node each task correctly sees
  `torch.cuda.device_count() == 4`.

**Verified at 16 GPUs:** DDP init succeeds (`All distributed processes
registered. Starting with 16 processes`), 16 per-rank app traces + 4 per-node
`service_<host>` counter traces, and categories `HIP_RUNTIME_API`,
`KERNEL_DISPATCH`, `MEMORY_COPY`, `SCRATCH_MEMORY`, `PAGE_MIGRATION`,
`RCCL_API`, `POSIX`, `STDIO` + app annotations. A standalone cross-node
all-reduce gate (4 ranks, 1/node) passes — run that gate BEFORE the workload.

**CORRECTED — it is NOT an OOM.** An earlier version of this note blamed a
memory exhaustion; a fully instrumented 16-rank run (per-sample cgroup, NUMA,
VRAM and per-process RSS) DISPROVES that. At the instant of the kill:

| metric | value |
|---|---|
| cgroup `memory.max` / `memory.current` / `memory.peak` | 512 GB / 18 GB / 19.5 GB |
| cgroup `memory.events` `oom` / `oom_kill` | **0 / 0** |
| node `MemTotal` / `MemAvailable` | 526 GB / 489 GB |
| per-NUMA free (all 4 domains, ~131 GB each) | 107–128 GB free |
| VRAM used per card (of 137 GB) | ~1.0 GB (0.7 %) |
| summed python RSS | 10–14 GB |

Nothing is near a limit and `oom_kill` is zero, so the exit-137 was never an
OOM kill. **Lesson: never infer OOM from exit 137 alone** — measure
`memory.events` from INSIDE the job's own cgroup (a login-side probe reads the
wrong cgroup entirely).

**Real failure chain (measured):** one LOCAL rank dies with a silent
`SIGABRT` (Lightning reports `Child process ... terminated with code -6`, and
the core file is named for the main `python` thread) → Lightning
"forcefully terminates all other processes", i.e. SIGKILLs its siblings, which
is the **exit 137** → peers see `ncclRemoteError: remote process exited` →
flux's `exit-timeout` SIGKILLs the last hung node. The abort emits no Python
traceback and no C++ `terminate called` message; `PYTHONFAULTHANDLER=1` did not
catch it either, consistent with an `abort()` inside a native library while
rocprofiler-sdk is attached. It reproduces at step ~19–36 of 40.

**The torch profiler is NOT the trigger** — the HIP-only 16-rank run (verified
`PP == 0`, profiler never fired) died the same way at `SeqNum=28`. Disabling
PyTorch's watchdog (`TORCH_NCCL_ASYNC_ERROR_HANDLING=0`,
`TORCH_NCCL_ENABLE_MONITORING=0`) removes the watchdog-initiated teardown but
the underlying rank abort remains, so the watchdog is a messenger, not a cause.

**Why `PP` was empty at 4 nodes — the actual reason.** `PP` events only exist
once a torch profiler cycle CLOSES and `on_trace_ready` (dftracer's
`trace_handler`) runs. Configuring `active` to span every step with `repeat=1`
means the single flush is scheduled AFTER the last step, so a run that dies at
step 19 of 40 emits **zero** PP — and even a successful run would produce one
huge end-of-run flush. To get PP for ALL steps, use REPEATING short cycles:
`schedule(wait=0, warmup=0, active=K, repeat=0)` (`repeat=0` = forever) so
`trace_handler` flushes every K steps. This is written and ready but NOT yet
executed (allocation expired) — it also collides with the known
"second `on_trace_ready` cycle re-arms roctracer" hazard, so it must be tested,
not assumed.

**How to apply:** for DL workloads, drive the torch profiler from ONE rank with
a short active window, and treat a non-profiling rank's abort as a
collective-desync symptom rather than a profiler bug. Single-rank tracing is the
reliable configuration today. Related: [[project-molformer-optimization]],
[[software-molformer]], [[software-tuolumne-pytorch-ddp-working-stack]].
