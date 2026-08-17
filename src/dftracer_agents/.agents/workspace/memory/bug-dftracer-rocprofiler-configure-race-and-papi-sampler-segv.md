---
name: bug-dftracer-rocprofiler-configure-race-and-papi-sampler-segv
description: FIXED: two dftracer bugs that made GPU tracing collect nothing and then SIGSEGV at teardown — force_configure loses a race to load-time constructors (use exported rocprofiler_configure), and the PAPI sampler was stopped after its own POSIX interception was freed
metadata:
  type: project
---

Two dftracer defects found and fixed on a Laghos/MFEM HIP session. Both were
silent: green status, wrong or corrupt data.

**1. GPU tracing never configured — `rocprofiler_force_configure` cannot win.**
`HIPFunction::initialize()` called it and DISCARDED the status. On Cray PE + ROCm
the load-time constructors of `librocprofiler-register` / `libamdhip64` / the Cray
MPICH GTL bring rocprofiler up BEFORE `main()` is entered, so configuration is
already locked when any application code runs — even `DFTRACER_CPP_INIT` as the
literal first statement of `main`. It returned
`ROCPROFILER_STATUS_ERROR_CONFIGURATION_LOCKED (16)` every run; zero GPU events.
A downstream symptom was `rocprofiler_start_context` returning
`CONTEXT_NOT_FOUND (2)`, because `tool_init` (which creates the context) never ran.

Fix: export the supported global `rocprofiler_configure` symbol from
`libdftracer_core.so`. rocprofiler scans loaded libraries for that exact symbol
and calls it at the right point in its own init, so there is no race. It returns
`nullptr` unless `DFTRACER_ENABLE` is set, so merely linking dftracer doesn't turn
a process into a profiling tool. Also zero-init `client_ctx`/`client_buffer` and
don't treat "context not created yet" as an error.

After the fix ALL FIVE buffer-tracing kinds fire (4 nodes x 16 GPUs):
KERNEL_DISPATCH 133,733, HIP_RUNTIME_API 46,150, MEMORY_COPY 32,
PAGE_MIGRATION 16, SCRATCH_MEMORY 16. **This overturns the earlier belief that
"4 of 5 kinds are broken under MPI while PAGE_MIGRATION works"** — the mechanism
was never MPI or KFD-vs-user-space, it was who won the registration race. See
[[system-tuolumne]].

**2. Teardown SIGSEGV that truncated traces — PAPI sampler re-enters its own
POSIX interception.** `DFTracerCore::finalize()` stopped the PAPI sampler AFTER
`posix_instance->unbind()/finalize()`. The sampler runs on its own libuv timer
thread and calls `PAPI_read()` each tick; PAPI reads its perf_event fd with
`read(2)`, which dftracer's own brahma/GOTCHA wrapper intercepts. In that window
the next sample died in `gotcha_get_wrappee()`:

```
#0 gotcha_get_wrappee()
#1 brahma::POSIXDFTracer::read(int, void*, unsigned long)
#2 read_wrapper(...)   #3 _pe_read()/PAPI_read()
#4 PAPICounterFunction::emit_sample()   #5 uv_run()/run_sampler()
```

Being a race it looked size-dependent: clean at ~335 steps, reliable SIGSEGV past
~600. Not cosmetic — it truncated the gzip stream on 11 of 16 ranks. Fix: finalize
the PAPI sampler BEFORE releasing the I/O bindings.

**Fix #2 is NOT on the `feature/papi-counter-tracing` branch (confirmed 2026-08-13).**
A miniFE session installed dftracer fresh from that GitLab branch and reproduced bug 2
exactly: 1-3 ranks of 16 SIGSEGV during teardown after all work completed, leaving
0-byte `*-app.pfw.gz` for those ranks. The build demonstrably HAS fix 1 —
`nm -D --defined-only libdftracer_core.so | grep rocprofiler_configure` shows it
exported, and GPU tracing worked — so the two fixes are not both on that branch.

Independent A/B on that session (identical pinned hosts and arguments, miniFE
openmp45-opt, 4 nodes x 4 ranks, nx=768): PAPI on -> SIGSEGV (13/16, 14/16, 15/16 over
three runs, different rank each time); `DFTRACER_ENABLE_PAPI_TRACING=0` -> clean 16/16;
pristine un-annotated binary -> clean; `DFTRACER_PAPI_EVENTS` cut to 5 fitting counters
-> still SIGSEGV, so it is not counter multiplexing. Duration-dependent exactly as the
race predicts: clean at nx=256 (17 s), crashes on ~4-minute runs.

**Before blaming a new bug, check the branch.** Verify the fix ordering in
`DFTracerCore::finalize()` (PAPI sampler stopped BEFORE `posix_instance->unbind()`)
rather than assuming any branch carrying PAPI support carries the fix.

**Why:** asked to enable PAPI + ROC profiler together on a GPU app; neither
worked, and fixing the first exposed the second.

**How to apply:** the deeper hazard remains — ANY dftracer-internal thread doing
libc I/O re-enters dftracer's own GOTCHA wrappers. A thread-local "internal"
flag honoured by the interceptors, or using the existing `POSIXBypass` on those
paths, would kill the bug class rather than this instance. Two methodology notes
that generalize: never discard a registration/config return status (one dropped
status hid this for an entire baseline), and bisect before hypothesising — two
plausible hypotheses (double teardown, rocprofiler shutdown order) were both
wrong; a length-vs-scale bisection plus a core dump named it exactly. See
[[project-laghos-papi-dftracer-baseline]] and [[workload-laghos]].
