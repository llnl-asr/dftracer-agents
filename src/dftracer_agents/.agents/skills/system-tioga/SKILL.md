---
name: system-tioga
description: System profile for Tioga (LLNL AMD MI250X / Cray PE, Flux) — how it differs from Tuolumne despite sharing the LC software stack, the queue that silently never schedules, the 8-GCD-per-node GPU ladder, the per-system PAPI preset set, and which Tuolumne lessons carry over unchanged. Load this for any dftracer session, build, or job launch on tioga.
---

# system-tioga

Tioga is an LLNL Cray EX system. It shares Tuolumne's **software** stack almost exactly
but **not** its hardware, so Tuolumne recipes port cleanly while Tuolumne *numbers* do
not. Getting that distinction wrong is the main way to waste a run here.

## Hardware — NOT the same as Tuolumne

| | Tioga | Tuolumne |
| --- | --- | --- |
| GPU | **MI250X**, `gfx90a` | MI300A (APU), `gfx942` |
| GPUs per node | **8 GCDs** | 4 |
| CPU | **AMD EPYC 7A53 (Trento)**, 64 cores / 128 threads | 96 cores |
| NUMA domains | 4 | |
| `craype` arch | `craype-x86-trento` | |

Consequences:

* The **GPU process-per-node ladder is 1,2,4,8** (8 GCDs), not Tuolumne's 1,2,4.
* Any OpenMP target-offload build must target **`gfx90a`**, not `gfx942`. A binary
  built for `gfx942` on Tuolumne will not run its kernels here.
* MI250X is a discrete GPU, not an APU, so host/device transfers are real PCIe/Infinity
  Fabric copies. `MEMORY_COPY` and `PAGE_MIGRATION` event volumes are not comparable
  with Tuolumne's.

## Queues — `pall` accepts jobs and never runs them

```
flux queue status
  pall: Job submission is enabled
  pall: Scheduling is stopped        <-- submits fine, sits forever
```

| Queue | Nodes | Time limit | Notes |
| --- | --- | --- | --- |
| `pdebug` (default) | 8 | **12 h** | the workhorse here — far more generous than Tuolumne's 1 h pdebug |
| `pllm` | 18 | 1 d | largest node count, but **often not authorized** — see below |
| `pci` | 4 | 2 h | |
| `pall` | 30 | 1 d | **scheduling stopped — do not use** |

**Always check `flux queue status`, not just `flux queue list`.** `flux queue list` shows
`pall` with healthy-looking limits; only `flux queue status` reveals that its scheduler is
stopped. A job submitted there stays `S` forever and looks like a busy cluster.

Because `pdebug` allows 12 h, the allocation-chaining that Tuolumne's 1 h limit forces is
usually unnecessary here — a whole sweep normally fits in one allocation.

### Advertised nodes are not available nodes — check drain state before planning a sweep

Tioga is a small machine and a large fraction of it is routinely **drained**. Observed
state during one sweep, for over 16 hours with no change:

```
free  pllm,pall     11    tioga[13-19,21-23,25]
free  pdebug,pall    2    tioga[34,39]          <-- pdebug advertises 8
down  pdebug,pall    6    tioga[35-38,40-41]
down  pci,pall       4    tioga[30-33]          <-- ALL of pci
down  pllm,pall      7    tioga[12,20,24,26-29]
```

So the queue table's node counts were fiction: `pdebug` could supply **2 of 8**, and
`pci` **0 of 4**. Any sweep cell needing ≥4 nodes was unrunnable, indefinitely.

**`pllm` may not be authorized for your account**, even though `flux queue list` shows
it enabled and with free nodes. The rejection only appears at submit time:

```
flux-alloc: ERROR: queue "pllm" not valid for user; valid queues for user: pdebug,pci
```

Check the three things before sizing an allocation, in this order — each catches a
failure the previous one misses:

1. `flux queue list` — limits and whether scheduling is enabled (`ST` column)
2. `flux resource list` — how many nodes are actually `free` vs `down` per queue
3. a throwaway `flux alloc` — the only way to learn your account's queue authorization

A sweep planned off (1) alone will sit in `SCHED` forever and read exactly like a busy
cluster. **Size the allocation to free nodes, not to the queue's advertised maximum**,
and when the largest remaining cells cannot fit in what is free, say so and stop rather
than chaining allocations that can never start — a driver retrying an impossible request
burns its allocation budget on `rc=1` and accomplishes nothing.

## Software stack — same as Tuolumne, and binaries are portable

Every module Tuolumne pins is present on tioga (verified): `rocm/6.4.2`,
`rocmcc/6.4.2-cce-20.0.0-magic`, `cray-mpich/9.0.1`, `papi/7.2.0.2`, `papi/7.3.0.1`.
ROCm is available up to `7.2.1`; default env is `PrgEnv-cray/8.7.0` + `cce/20.0.0`.

A dftracer built on Tuolumne **runs unmodified on tioga** (verified: the Tuolumne-built
`dftracer_split` executes here). The `/usr/WS2` and `/usr/workspace` filesystems are
shared, so a session workspace created on one machine is directly usable on the other.
This makes tioga a legitimate place to post-process a Tuolumne corpus.

## Parallel filesystems are NOT shared with Tuolumne

`/usr/WS2` and `/usr/workspace` (NFS) **are** shared, so a session workspace crosses
machines fine. The **Lustre** mounts do not: tioga has `/p/lustre1` and `/p/lustre2`,
while Tuolumne uses `/p/lustre5`.

**`/p/lustre1` is tioga's main Lustre — use `/p/lustre1/$USER` for application data.**
`/p/lustre2` is also mounted and writable but is not the default choice. Both are ~24 PB.
Per [[feedback-lustre-io]]: application data (datasets, checkpoints, per-run output) goes
to Lustre; dftracer **traces** stay in the session workspace under `genesis_traces/`,
never on Lustre.

This bites whenever a session's `dataset/` is a symlink onto the other machine's Lustre:
the traces (in the workspace, on NFS) are readable here, but the **application's own
output** (checkpoints, result files) silently is not — the symlink dangles. Any audit
that parses app results must run where that PFS is mounted. Check with
`ls -d /p/lustre*` before assuming a session is fully usable from here.

## PAPI — re-derive the counter set, never copy Tuolumne's

Same budget, **different preset set**:

| | Tioga | Tuolumne |
| --- | --- | --- |
| Hardware counters | 5 | 5 |
| Presets available | **27** | 30 |
| Derived presets | **5** | 12 |

Tioga's derived presets (each costs >= 2 of the 5 counters):
`PAPI_TLB_IM`, `PAPI_BR_UCN`, `PAPI_BR_TKN`, `PAPI_BR_NTK`, `PAPI_BR_PRC`

The 27 available presets:

```
PAPI_BR_CN    PAPI_BR_INS   PAPI_BR_MSP   PAPI_BR_NTK*  PAPI_BR_PRC*
PAPI_BR_TKN*  PAPI_BR_UCN*  PAPI_FAD_INS  PAPI_FDV_INS  PAPI_FML_INS
PAPI_FP_INS   PAPI_FP_OPS   PAPI_FSQ_INS  PAPI_L1_DCA   PAPI_L1_DCM
PAPI_L2_DCH   PAPI_L2_DCM   PAPI_L2_DCR   PAPI_L2_ICA   PAPI_L2_ICH
PAPI_L2_ICM   PAPI_L2_ICR   PAPI_TLB_DM   PAPI_TLB_IM*  PAPI_TOT_CYC
PAPI_TOT_INS  PAPI_VEC_INS                       (* = derived)
```

Tioga does **not** have `PAPI_L2_TCM`, `PAPI_L2_TCH`, or `PAPI_FMA_INS`, all of which
exist on Tuolumne. Conversely several presets that are *derived* on Tuolumne
(`PAPI_L2_ICM`, `PAPI_FP_INS`, `PAPI_FML_INS`, `PAPI_FAD_INS`, `PAPI_FDV_INS`,
`PAPI_FSQ_INS`) are **native** here, so they cost one counter instead of two and pack
differently.

A Tuolumne counter partition therefore fails on tioga twice over: it requests presets
that do not exist, and it mis-sizes the sets. Derive the partition from this machine's
`papi_avail` and probe-verify `args.multiplex == 0`. See [[software-papi]].

The `papi/7.3.0.1` vs `7.2.0.2` question is open here: on Tuolumne 7.3.0.1 SIGSEGVs at
`PAPI_library_init` because its `rocp_sdk` component is ABI-incompatible with ROCm 6.4.2
(see [[bug-cray-papi-731-rocp-sdk-abi-segfault]]). Tioga has newer ROCm available, so
7.3.0.1 *may* be fine — but **probe it before trusting it**; `7.2.0.2` is the known-safe
pin and is present.

## dftracer BufferManager init race — FIXED, but check your build has it

Any traced multi-rank GPU run here SIGSEGV'd at **>= 3 total ranks** (clean at 1-2,
reproducible 3/3, invisible under gdb) until a dftracer fix landed. Root cause was not
tioga-specific but tioga exposes it: `BufferManager` uses two-phase construction, so
`Singleton<BufferManager>::get_instance()` publishes a pointer whose `config` is still
null until `initialize()` runs. rocprofiler — registered at load time via the exported
`rocprofiler_configure`, before dftracer initialises — flushes from its own thread pool
into that window and dereferences null in `log_data_event()`.

The fix adds a `ready` atomic to `BufferManager`, set at the end of `initialize()` and
cleared at the start of `finalize()`, guarding all three `log_*_event` entry points. It
covers every asynchronous producer (HIP, CUDA, PAPI sampler, brahma), not just HIP.

If a multi-rank run here dies at init with no output, confirm your dftracer build carries
that guard before looking anywhere else.

## Reserve a core for dftracer_service when sizing per-rank cores

A tioga node has **64 cores** and `dftracer_service` holds **one per node** for the whole
run. Sizing per-rank cores as `64 / max_ppn` therefore makes the largest ppn request every
core on the node, and the application job can never be scheduled -- it sits in `S`
**forever** rather than failing. Measured: at ppn=8, 8 cores/rank deadlocks and 7 works.
Use `floor((cores_per_node - 1) / max_ppn)`.

## What carries over from Tuolumne unchanged

These are software-stack behaviours, not hardware, so they apply here as-is:

* `dftracer_service start` under `flux run` never returns — use `flux submit` detached
  and poll the per-host pid files. See [[bug-dftracer-service-start-blocks-flux-run]].
* Service start / app / service stop must all be pinned to the **same** hosts with
  `--requires=host:`, or in an allocation larger than the job they each get a different
  node subset. See [[bug-dftracer-service-hosts-must-be-pinned]].
* `DFTRACER_BUILD_VARIORUM=AUTO` finds a system variorum RPM that reports
  `_ERROR_VARIORUM_UNSUPPORTED_PLATFORM` and yields **zero** power events; use
  `DFTRACER_BUILD_VARIORUM=ALWAYS`. Verify a `gpu`/`power` event actually lands.
* The `dftracer_service` process maps two `librocm_smi64` ABIs (`.so.1` via libhwloc,
  `.so.7` via libvariorum) and aborts at teardown with `corrupted size vs. prev_size in
  fastbins`. It is strictly **post-flush** and costs no data. `HWLOC_COMPONENTS=-rsmi`
  does not avoid it (`.so.1` arrives via a link-time `DT_NEEDED`).
* ROCProfiler logs `could not be locked for profiling due to lack of permissions
  (capability SYS_PERFMON)`. GPU **activity** tracing (kernel dispatch, memory copy,
  page migration) is unaffected; GPU **hardware counters** are unavailable.

## Related

[[system-tuolumne]] for the sibling MI300A system, [[software-papi]] for the counter
partition method, [[software-rocm]] for HIP/rocprofiler generics, [[flux-alloc]] for
allocation mechanics, [[genesis_run]] for the sweep harness that consumes all of this.
