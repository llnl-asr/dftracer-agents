---
name: project-lammps-kokkos-hip-full-feature-baseline
description: LAMMPS+KOKKOS/HIP annotate + maximum-feature dftracer baseline on Tuolumne (MI300A) — 2 nodes/8 GPUs, 5.53M events with all layers present; found a Cray-GTL/ROCm soname blocker and a dftracer finalize() symbol collision
metadata:
  type: project
---

LAMMPS (lammps/lammps develop) with the KOKKOS package, annotated in dftracer
FUNCTION mode and traced with the maximum feature set on Tuolumne (AMD MI300A APU,
Cray PE, Flux, ROCm 7.2.1, PAPI 7.2.0.2).

Full detail lives in the `workload-lammps-kokkos` skill — this is a pointer plus the
findings that generalize beyond LAMMPS.

**Result.** 2 nodes x 4 MI300A GPUs (8 ranks), 1,024,000-atom LJ melt, 1000 steps,
rc=0, 4.23 s loop time. ~5.53M events. Every dftracer layer PRESENT: app annotation
(CPP_APP 4,186,332), GPU (HIP_RUNTIME_API 719,842 / KERNEL_DISPATCH 159,965 /
MEMORY_COPY 64,864 / SCRATCH_MEMORY 8), MPI (p2p 295,632, collective 1,504), STDIO
84,284, POSIX 6,176, node counters (sys/net/io), PAPI (type 11) and Variorum power
(type 13, cat=gpu, real watts). PAPI multiplex=0 with 4 counters in 5 slots.

**Generalizable findings**

1. GitHub `develop` dftracer has NO `DFTRACER_ENABLE_PAPI_TRACING` /
   `DFTRACER_ENABLE_VARIORUM` cmake options at all — cmake reports them as
   `unused-cli` and the install is green with those features silently absent. The
   LLNL czgitlab `develop` has them. Always grep the installed
   `dftracer_config.hpp` for `DFTRACER_PAPI_TRACING_ENABLE` /
   `DFTRACER_VARIORUM_ENABLE`, and `nm -D` for an exported `rocprofiler_configure`.

2. `dftracer.h` declares a global `void finalize();` and `DFTRACER_CPP_FINI()`
   expands to `finalize()`. Any app with its own file-local `static void finalize()`
   either fails to build or, worse, silently binds FINI to the APP's function — the
   tracer never flushes and every trace is 0 bytes with rc=0. Check for a `finalize`
   symbol collision in any entry-point file before trusting an empty trace.

3. A GPU-transport library built against a different ROCm major than the app is a
   hard startup failure (`libamdhip64.so.6: cannot open shared object file`) that
   affects the PRISTINE binary too, so it is easy to misattribute to annotation.
   Compare `objdump -p` sonames across the site's MPI versions and take just the
   matching transport library. Dropping the transport library instead is not a fix:
   it trades the load error for `cxil_map: write error` at run time.

4. The clang annotator's AST cost filter drops one-line DISPATCHER functions
   (`Comm::forward_comm`, `Neighbor::build`, `Modify::initial_integrate`) that are
   the actual per-timestep hot path, while it DOES annotate per-element setup
   helpers that then dominate the trace (two of them produced 89% of all app
   events here). Always read the *skipped* list and the fired-event histogram, not
   just the annotated count.

5. The annotator inserts host tracing macros into Kokkos `KOKKOS_INLINE_FUNCTION`
   device bodies, and places the `#include` after the LAST `#include` in a file —
   which lands inside namespace scope in files with mid-file style-header includes.
   Verify both after every annotate call.

6. Finishing a slow GPU build on a compute node inside the allocation you already
   need is roughly an order of magnitude faster than a saturated shared login node.

**Correctness.** Annotated-with-`DFTRACER_ENABLE=0` reproduced the traced run's final
thermo exactly (temp 0.70381953, TotEng -4.6204787); tracing overhead at maximum
feature capture was ~4.5x on loop time, dominated by HIP runtime interception.

**Why:** asked to prove which dftracer event types a Kokkos/HIP MD code can actually
produce, end to end, on MI300A.

**How to apply:** load [[workload-lammps-kokkos]] before any LAMMPS or Kokkos-on-HIP
session. See also [[system-tuolumne]], [[software-rocm]], [[software-papi]],
[[bug-dftracer-rocprofiler-configure-race-and-papi-sampler-segv]],
[[feedback-never-prebuilt-when-config-knobs-needed]].
