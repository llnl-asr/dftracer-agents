---
name: bug-dftracer-variorum-linked-into-app-heap-corruption
description: FIXED: variorum in the global DEPENDENCY_LIB linked libvariorum+rocm_smi into every traced APP, loading two rocm_smi ABIs (hwloc .so.1 + ROCm .so.7) and corrupting the heap at exit — aborted all ranks even with DFTRACER_ENABLE=0
metadata:
  type: project
---

On the `feature/variorum-power-tracing` branch, every traced application aborted
at exit with glibc `corrupted size vs. prev_size in fastbins`, once per rank,
immediately after the app's own output finished.

**Root cause:** variorum was appended to the global CMake `DEPENDENCY_LIB`, which
is linked into `dftracer_core` — the library every traced app links. Variorum is
documented as service-side only ("sampled by dftracer_service ... not inside a
traced process"), so this dragged `libvariorum` + `libjansson` + `librocm_smi64`
into the application. On an MI300A node that put **two different rocm_smi ABIs in
one process**:

```
librocm_smi64.so.1 => /usr/lib64/hwloc/librocm_smi64.so.1   (hwloc's)
librocm_smi64.so.7 => /opt/rocm-6.4.2/lib/librocm_smi64.so.7 (variorum's)
```

Two copies of the same library allocating and freeing across each other is a
textbook heap-corruption recipe. Fixed upstream by commit `3035768` "Link
variorum only into the service that uses it": a dedicated
`DFTRACER_VARIORUM_LINK_LIBS` attached to the service telemetry target instead of
`DEPENDENCY_LIB`. After the fix the app's library set is byte-identical to a
laghos built without dftracer at all, and runs exit 0.

**The diagnosis path is the transferable part**, because the obvious signals all
pointed the wrong way:
- The abort backtrace was useless — `exit()` -> `__run_exit_handlers` ->
  `_int_free` -> `malloc_consolidate`. Heap corruption is *detected* at teardown
  but *caused* much earlier.
- It reproduced with **`DFTRACER_ENABLE=0`**, which rules out the sampler, the
  tracing runtime and every collector, and points at mere linkage.
- It reproduced at **1 rank**, ruling out MPI.
- Minimal programs that linked `libdftracer_core` and called INIT/FINI/REGION/
  FUNCTION were all **clean** — because they never load hwloc's rocm_smi plugin.
  Only the real app, which pulls hwloc, triggers it.
- The decisive test was the **control**: the same laghos built WITHOUT dftracer
  was clean, while the annotated one aborted with tracing off. That is what
  turned "some teardown bug" into "linkage".

**Why:** found while cycling PAPI counter groups on Laghos with Variorum power
enabled; every one of the 9 planned runs was aborting.

**How to apply:** when a linked-in profiler causes corruption at exit, do not
start from the backtrace. Bisect by *linkage* — disable the feature at run time,
drop to one rank, then compare `ldd` of the instrumented binary against an
uninstrumented control and look for a library appearing twice under different
sonames. See [[project-laghos-papi-dftracer-baseline]] and
[[bug-dftracer-rocprofiler-configure-race-and-papi-sampler-segv]] for the two
earlier dftracer teardown bugs from the same session.
