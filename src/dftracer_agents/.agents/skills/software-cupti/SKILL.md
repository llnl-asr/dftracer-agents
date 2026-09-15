---
name: software-cupti
description: >
  CUPTI (the CUDA Profiling Tools Interface) as dftracer's NVIDIA GPU tracing
  backend — what the three CUPTI APIs are and which one dftracer uses, where
  CUPTI lives in a CUDA toolkit, the versioned-activity-record trap that makes
  "just use the newest struct" fail to compile, clock alignment onto the
  dftracer timeline, the emitted event taxonomy, and the run-time gates
  (one-profiler-per-process, driver profiling permissions) that make CUPTI
  silently collect nothing. Load this skill for any
  DFTRACER_ENABLE_CUDA_TRACING build, any NVIDIA-GPU dftracer session, or any
  "GPU events are missing from the trace" investigation on an NVIDIA system.
---

Cross-references: [[software-rocm]] [[dftracer-install]] [[software-papi]] [[tools-dftracer]] [[dftracer-preload-run]]

CUPTI is the NVIDIA analogue of rocprofiler-sdk. Everything in [[software-rocm]]
about *why* GPU tracing needs its own backend applies here; this skill is the
NVIDIA half. The two backends are deliberately built to the same shape, so a
trace may contain both `HIP` and `CUDA` events on one timeline.

---

## 1. What CUPTI is, and which part of it dftracer uses

CUPTI ships **inside the CUDA toolkit** — there is no separate package to
install, no separate license, and no daemon. It exposes four distinct APIs, and
conflating them is the most common source of confusion:

| API | What it gives you | Overhead | dftracer uses it? |
|---|---|---|---|
| **Activity API** | Asynchronous *records* of things that already happened — kernels, memcpys, API calls — delivered in bulk on a CUPTI worker thread | Low; async, buffered | **YES — this is the whole backend** |
| **Callback API** | Synchronous callbacks *at* each CUDA runtime/driver API entry/exit | Higher; on the critical path | No |
| **Event/Metric API** (legacy) | GPU hardware counters, pre-Volta style | High; serializes kernels | No |
| **Profiling / PerfWorks API** | Modern hardware counters (what `ncu` uses); replays kernels | Very high; replays kernels | No |

**The practical consequence:** dftracer's CUDA backend gives you a *timeline*
(what ran, when, how long, how many bytes), **not hardware counters**. If you
want NVIDIA GPU hardware counters you need `ncu`/PerfWorks, which is a different
tool with kernel replay. Do not expect `DFTRACER_ENABLE_CUDA_TRACING=ON` to
produce anything counter-shaped — that is [[software-papi]]'s job on the CPU
side, and PAPI's CPU presets are the wrong instrument for the GPU regardless.

The Activity API contract dftracer implements:

1. `cuptiActivityRegisterCallbacks(buffer_requested, buffer_completed)`
2. `cuptiActivityEnable(KIND)` once per activity kind wanted
3. CUPTI calls `buffer_requested` when it needs memory; you `malloc` and hand it back
4. CUPTI calls `buffer_completed` on its own worker thread with a full buffer;
   you walk it with `cuptiActivityGetNextRecord` and `free` it
5. `cuptiActivityFlushAll(1)` at teardown to drain in-flight buffers

---

## 2. Where CUPTI lives — two toolkit layouts

NVIDIA moved CUPTI once, and both layouts are still in the wild:

| CUDA version | Header | Library |
|---|---|---|
| **≤ 10.1** | `<cuda>/extras/CUPTI/include/cupti.h` | `<cuda>/extras/CUPTI/lib64/libcupti.so` |
| **≥ 10.2** | `<cuda>/include/cupti.h` | `<cuda>/lib64/libcupti.so` |

`cmake/modules/FindCUPTI.cmake` handles both, and resolves the CUDA root from
the first of these that *actually contains `cupti.h`*:

1. `DFTRACER_CUDA_PATH` (explicit dftracer override — never silently overridden)
2. `CUDAToolkit_ROOT` / `CUDA_TOOLKIT_ROOT_DIR` CMake vars
3. `CUDAToolkit_ROOT` / `CUDA_HOME` / `CUDA_PATH` env vars (what `module load cuda/...` sets)
4. CMake's own `find_package(CUDAToolkit)`
5. `nvcc` on `PATH`
6. `/usr/local/cuda`

> **TRAP — a CUDA toolkit can exist without CUPTI in it.** A stripped or
> driver-only CUDA install has `bin/nvcc`, `include/`, `lib64/` and **no
> `cupti.h` and no `extras/`**. `FindCUPTI` correctly skips it and walks to the
> next hint, so the build succeeds with CUDA tracing silently *off*. Always
> confirm from the configure line, never from "CUDA is installed":
> ```
> -- [DFTRACER] found CUPTI at <root>/include (cuda root <root>)
> -- [DFTRACER] CUDA toolkit version: 12.6.0
> ```
> Seen in the wild: a site-default `/usr/local/cuda-12.0` with no `extras/` and
> no `cupti.h`, while the module-managed `<tce>/cuda/cuda-12.6.0` had CUPTI in
> the merged layout. Pin it with `DFTRACER_CUDA_PATH` rather than relying on
> `/usr/local/cuda` winning the search.

> **TRAP — `find` does not follow symlinks.** Site CUDA trees are usually
> symlinks into a shared collab filesystem. `find /path/to/cuda* -name cupti.h`
> returns nothing and looks like proof CUPTI is absent. Use `find -L`, or just
> `ls -L <root>/include/cupti.h`.

> **NOT CUPTI:** `libcupti.so.10.2`, `libcupti.so.11.x` … shipped inside
> `nsight-systems/*/target-linux-x64/`. Those are Nsight's own bundled
> injection libraries, with no headers and no SDK. They cannot be built
> against; ignore them when hunting for a usable CUPTI.

---

## 3. Building dftracer with CUPTI

`DFTRACER_ENABLE_CUDA_TRACING` is a **compile-time** option and defaults to
`OFF`. Like every other dftracer feature flag it is read by `setup.py` from an
**environment variable**, not from `CMAKE_ARGS` — see
`feedback-dftracer-install-env-vars`.

```bash
# pip / setup.py  (the session path)
DFTRACER_ENABLE_CUDA_TRACING=ON \
DFTRACER_CUDA_PATH=<tce>/cuda/cuda-12.6.0 \
  pip install --no-binary dftracer <source-or-git-url>

# autobuild
./autobuild.sh --with-cuda <tce>/cuda/cuda-12.6.0     # --with-cuda implies --enable-cuda

# plain cmake
cmake -DDFTRACER_ENABLE_CUDA_TRACING=ON -DDFTRACER_CUDA_PATH=<root> ..
```

`DFTRACER_ENABLE_DYNAMIC_DETECTION=ON` turns CUPTI on automatically whenever it
is found, without naming `DFTRACER_ENABLE_CUDA_TRACING`.

**Verify the backend is actually compiled in** — from the trace, not from pip's
exit status. The `DFTRACER` metadata record carries a `build` key; CUPTI
support shows as `"cuda": 1`.

---

## 4. The versioned-activity-record trap (the reason this backend is fiddly)

NVIDIA versions every activity record struct: `CUpti_ActivityKernel4`,
`…Kernel5`, … `…Kernel11`. When a layout changes they **append a new numbered
typedef**, and a few releases later they **delete the superseded ones outright**
(CUDA 12.6 removed everything below its current version).

Therefore:

* "always use the newest struct" → **fails to compile on older toolkits**
* "always use the oldest struct" → **fails to compile on newer toolkits**

dftracer selects the struct set per toolkit version at compile time, keyed on
`DFTRACER_CUDA_VERSION`, which CMake bakes into `dftracer_config.hpp` from the
detected `CUDAToolkit_VERSION`.

> **Why keyed on the toolkit version and not `CUPTI_API_VERSION`:** CUDA 11.5
> and 11.6 **share `CUPTI_API_VERSION` 16** but ship different newest-kernel
> structs (`Kernel6` vs `Kernel7`). Keying on the CUPTI API version is subtly
> wrong and breaks exactly on that pair.

The bands as implemented (verified against CUDA 10.1, 10.2, 11.1–11.8, 12.2,
12.6, 12.9, 13.1):

| Toolkit | Kernel | Memcpy | MemcpyP2P | Memset | Memory | Sync | Overhead | UnifiedMem | GraphTrace |
|---|---|---|---|---|---|---|---|---|---|
| ≥ 13.0 | Kernel11 | Memcpy6 | PtoP4 | Memset4 | Memory4 | Sync2 | Overhead3 | UM3 | Graph2 |
| ≥ 12.9 | Kernel9 | Memcpy6 | PtoP4 | Memset4 | Memory4 | Sync2 | Overhead3 | UM3 | Graph2 |
| ≥ 12.6 | Kernel9 | Memcpy5 | PtoP4 | Memset4 | Memory4 | Sync | Overhead3 | UM2 | Graph2 |
| ≥ 12.0 | Kernel9 | Memcpy5 | PtoP4 | Memset4 | Memory3 | Sync | Overhead | UM2 | Graph |
| ≥ 11.8 | Kernel8 | Memcpy5 | PtoP4 | Memset4 | Memory3 | Sync | Overhead | UM2 | Graph |
| ≥ 11.6 | Kernel7 | Memcpy5 | PtoP4 | Memset4 | Memory3 | Sync | Overhead | UM2 | (11.7+) Graph |
| ≥ 11.2 | Kernel6 | Memcpy4 | PtoP3 | Memset3 | Memory2 | Sync | Overhead | UM2 | — |
| ≥ 11.0 | Kernel5 | Memcpy4 | PtoP3 | Memset3 | — | Sync | Overhead | UM2 | — |
| 10.x | Kernel4 | Memcpy2 | — | Memset | — | Sync | Overhead | UM2 | — |

`GRAPH_TRACE` landed in **11.7**, i.e. *inside* the 11.6 band, so it carries its
own `#if` gate rather than a band of its own. Kinds whose struct is absent for
the selected band are `#ifdef`-gated out of `initialize()` entirely.

**If you add support for a new toolkit:** add a band, do not widen an existing
one, and re-check every struct in the row — NVIDIA does not bump them in step.

---

## 5. Clock alignment — why GPU events land on the CPU timeline

CUPTI timestamps are **nanoseconds on CUPTI's own monotonic clock**.
dftracer's own events come from `gettimeofday`, rendered in the configured
`time_metric` unit. The two are **not comparable as-is** — untranslated CUPTI
timestamps put GPU work decades away from the CPU events that launched it.

The backend rebases every record:

```
factor    = time_metric_units_per_second(time_metric) / 1e9
time_diff = logger->get_time() - floor(cupti_now * factor)      // sampled ONCE
event_ts  = floor(cupti_ts * factor) + time_diff
```

Two details that matter:

* `time_diff` is resolved **lazily, on first use**, because `cuptiGetTimestamp()`
  only returns a usable value once CUPTI is initialized. If it is not yet
  ready, the offset is left unresolved and retried on the next record — it is
  not an error.
* Durations use `transform_time(end, start)`, which floors *both* endpoints
  before subtracting, so a duration never drifts against the two timestamps.

The HIP/rocprofiler backend does exactly the same thing, which is what lets a
single trace mix AMD and NVIDIA GPU events with CPU-side I/O and MPI coherently.
`test/cuda/check_cuda_trace.py` asserts the property that actually matters:
**CUDA events fall inside the application region that issued them.** If they do
not, suspect the clock rebase before suspecting the annotation.

---

## 6. What ends up in the trace

All CUDA events are written with event type `CUDA` and a `cat` naming the
activity kind:

| Category | Events |
|---|---|
| `CUDA_RUNTIME_API` | CUDA runtime API calls (`cudaMalloc`, `cudaMemcpy`, …) |
| `CUDA_DRIVER_API` | CUDA driver API calls (`cuLaunchKernel`, …) |
| `CUDA_KERNEL` | Kernel launches, named by the kernel symbol |
| `CUDA_MEMCPY` | Host/device copies, named by direction |
| `CUDA_MEMCPY_P2P` | Peer-to-peer device-to-device copies |
| `CUDA_MEMSET` | Device memsets |
| `CUDA_MEMORY` | Device allocations and frees |
| `CUDA_SYNC` | Stream / event / context synchronization |
| `CUDA_GRAPH` | CUDA graph execution |
| `CUDA_MARKER` | NVTX markers |
| `CUDA_UNIFIED_MEMORY` | UM page faults, migrations, thrashing |
| `CUDA_OVERHEAD` | CUPTI's own tracing overhead |

Every event carries `device_id`, `context_id`, `stream_id`, `correlation_id`;
copies add `bytes` and `copy_kind`; kernels add `grid_x`/`block_x`,
`registers_per_thread` and shared-memory sizes.

**`correlation_id` is the join key** — it links a GPU-side record back to the
API call that issued it. That is how you measure launch-to-execute latency and
how you attribute a kernel to the host region that launched it.

Three kinds are enabled but deliberately **not emitted as events** — `NAME`,
`DEVICE`, `CONTEXT`, `EXTERNAL_CORRELATION`. They keep CUPTI's internal
name/id tables populated so the records that *are* emitted resolve to real
names. Disabling them to "reduce noise" yields unnamed kernels.

Kernel and marker names are truncated to **64 chars**, matching the HIP backend
so both vendors produce comparably sized names. C++ template-heavy kernel
symbols will therefore appear cut off; that is intentional, not corruption.

---

## 7. Buffers and dropped records

* Buffer size is **8 MiB** (NVIDIA's own sample default), 8-byte aligned,
  `max_num_records = 0` meaning "fill the buffer completely".
* `buffer_completed` runs on a **CUPTI worker thread**, not the app thread —
  anything it touches must be thread-safe.
* After draining, the backend calls `cuptiActivityGetNumDroppedRecords` and
  logs `CUPTI dropped N activity records` at WARN.

> **Read the warning; do not ignore it.** Dropped records mean the trace is
> *incomplete*, and CUPTI drops silently at the API level — only that log line
> tells you. It happens on kernel-storm workloads where the app produces
> records faster than `buffer_completed` retires them. Mitigations, in order:
> trace fewer activity kinds, shorten the run, or raise the buffer size.

`finalize()` calls `cuptiActivityFlushAll(1)` so in-flight buffers drain
**while the logger is still alive**. A backend that skips this loses the tail
of the run — typically the most interesting part.

---

## 8. Run-time gates that make CUPTI collect nothing

Nothing extra is needed at run time for a CUPTI-enabled build — GPU tracing
starts with dftracer. But two gates fail *silently or confusingly*:

### 8.1 One profiling client per process

**CUPTI allows only one profiling client per process.** Running under another
CUPTI-based profiler at the same time will make one of them fail to attach:

* `nsys` (Nsight Systems)
* `ncu` (Nsight Compute)
* framework profilers — `torch.profiler` / `torch.autograd.profiler` with the
  CUDA activity enabled, TensorFlow's profiler, JAX's

This is the exact NVIDIA counterpart of the ROCm conflict recorded in
`bug-dftracer-torch-profiler-rocprofiler-conflict`. On the AMD side the two can
coexist for one window; on the NVIDIA side, assume they cannot coexist at all.
Pick one.

### 8.2 Driver profiling permission

NVIDIA gates non-root profiling behind a driver module parameter. When it is
restricted, CUPTI returns `CUPTI_ERROR_INSUFFICIENT_PRIVILEGES` and no GPU
events appear:

```bash
# 0 = any user may profile (what you want); 1 = admin only
grep -rh RestrictProfiling /etc/modprobe.d/
cat /proc/driver/nvidia/params | grep -i RestrictProfiling
```

This is set at driver load, so it cannot be changed from inside a job — if it
is `1`, profiling needs a site admin, full stop. **Check it before planning a
GPU-tracing campaign**, not after the first run comes back empty.

### 8.3 Cheap pre-flight probe

Before committing to a build, confirm CUPTI attaches on a *compute* node
(login nodes usually have no GPU and no driver):

```c
#include <cupti.h>
#include <stdio.h>
int main(void){
  uint64_t ts=0; const char *msg=0;
  CUptiResult r = cuptiGetTimestamp(&ts);
  printf("cuptiGetTimestamp -> %d ts=%llu\n", r, (unsigned long long)ts);
  r = cuptiActivityEnable(CUPTI_ACTIVITY_KIND_CONCURRENT_KERNEL);
  cuptiGetResultString(r,&msg);
  printf("cuptiActivityEnable -> %d (%s)\n", r, msg?msg:"?");
  uint32_t v=0; cuptiGetVersion(&v); printf("CUPTI_API_VERSION=%u\n", v);
  return 0;
}
```
```bash
gcc probe.c -I$CUDA_ROOT/include -L$CUDA_ROOT/lib64 -lcupti -o probe
LD_LIBRARY_PATH=$CUDA_ROOT/lib64:$LD_LIBRARY_PATH ./probe
```
All three succeeding (`0`, non-zero ts, `CUPTI_SUCCESS`) means the backend will
work. `CUPTI_API_VERSION` also pins which struct band you are in — 24 = CUDA 12.6.

---

## 9. Feature-matrix caveat: CUPTI and PAPI/variorum may not be on one branch

CUPTI support and the PAPI-counter / variorum-power / service-telemetry
features were developed on **separate branches** and, as of 2026-08-26, had not
been merged:

| Branch | CUPTI | MPI | PAPI | Variorum | Service telemetry |
|---|---|---|---|---|---|
| `feature/cupti` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `develop` (gitlab) | ❌ | ✅ | ✅ | ✅ | ✅ |

**Check before promising a run configuration that needs both.** The
authoritative test is not the branch name but:

```bash
grep -c 'DFTRACER_ENABLE_PAPI_TRACING\|DFTRACER_ENABLE_VARIORUM' CMakeLists.txt
grep -c 'DFTRACER_ENABLE_CUDA_TRACING' CMakeLists.txt
```

An unmerged combination means either merging the branches yourself or dropping
one capability from the plan — decide with the user, do not silently ship a
build missing a feature they asked for.

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Build succeeds, no `CUDA` events, `"cuda"` absent from trace `build` key | `DFTRACER_ENABLE_CUDA_TRACING` never reached cmake | It is an **env var** for `setup.py`, not `CMAKE_ARGS`. Re-check the configure log for `found CUPTI at …` |
| `found CUPTI` line absent, CUDA is "installed" | Toolkit has no `cupti.h` (driver-only/stripped install) | Pin `DFTRACER_CUDA_PATH` at a toolkit that really has it; verify with `ls -L <root>/include/cupti.h` |
| `find` says no `cupti.h` anywhere | `find` not following the site symlink | `find -L`, or `ls -L` the path directly |
| Compile errors on `CUpti_ActivityKernelN` unknown type | Struct band wrong for this toolkit | `DFTRACER_CUDA_VERSION` mis-detected; check the version parsed from `cuda.h`, add a band if the toolkit is new |
| `CUPTI_ERROR_INSUFFICIENT_PRIVILEGES` | `NVreg_RestrictProfilingToAdminUsers=1` | Site-admin change; cannot be worked around from a job |
| Events appear but at absurd timestamps, outside every app region | Clock rebase did not resolve | `cuptiGetTimestamp` returned 0/failed at first record; confirm CUPTI initialized before the first traced GPU op |
| `CUPTI dropped N activity records` | Record production outran buffer retirement | Fewer activity kinds, shorter run, or larger buffer |
| One of dftracer / nsys / ncu / torch.profiler fails to attach | One CUPTI client per process | Run them in separate runs |
| Kernel names truncated at 64 chars | By design, matches HIP backend | Not a bug |
| Unnamed kernels / missing symbols | `NAME`/`DEVICE`/`CONTEXT` kinds disabled | Re-enable them; they populate CUPTI's name tables |

---

## 11. Site note: NVIDIA H100 + Sapphire Rapids cluster (`matrix`-class)

Measured on a compute node of an LLNL H100 cluster, 2026-08-26:

* 4 × NVIDIA H100 80GB HBM3 per node, compute capability 9.0, driver 610.43.02
* 2 × Intel Xeon Platinum 8480+ (Sapphire Rapids), 112 cores, **8 NUMA regions**,
  `RestrictedCoresPerGPU=28` → 28 cores per GPU
* Site-default `/usr/local/cuda-12.0` **has no CUPTI**; the module-managed
  toolkits (`cuda/11.8.0`, `12.2.2`, `12.6.0`, `12.9.1`, `13.1.1`) all carry it
  in the merged layout. **Pin `DFTRACER_CUDA_PATH`.**
* `NVreg_RestrictProfilingToAdminUsers=0` → **non-root profiling is permitted**
* Probe result against `cuda-12.6.0`: `cuptiGetTimestamp` SUCCESS,
  `cuptiActivityEnable(CONCURRENT_KERNEL)` SUCCESS, `CUPTI_API_VERSION=24`
* **PAPI here is 5.6.0.0 with only 17 presets** across 19 counters
  (`perf_event`, PMU `spr`) — *not* the ~30 presets an MI300A/Cray node reports.
  Any plan that says "capture all 30 PAPI counters" must be re-derived per
  system. `rapl` is disabled ("CPU model not supported") and
  `perf_event_uncore` is disabled for lack of permissions, so **CPU/DRAM energy
  is not available via PAPI here**. There is no PAPI CUDA/NVML component.
