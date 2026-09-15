---
name: project-sw4-dftracer-full-feature-baseline
description: SW4 (geodynamics seismic wave code) annotate + 2-node full-feature dftracer baseline on Tuolumne — COMPLETE, 1.16M events with PAPI multiplex=0, MPI, POSIX/STDIO, node counters and variorum power; found the `.C`-extension annotator trap and two annotator placement bugs
metadata:
  type: project
---

SW4 (geodynamics/sw4, 4th-order 3D seismic wave propagation, C++ + Fortran-77,
MPI+OpenMP) annotated with dftracer FUNCTION mode and baseline-traced on
Tuolumne MI300A at 2 nodes with PAPI counters, MPI/POSIX/STDIO interception, the
per-node `dftracer_service` daemon, and Variorum power.

**Status: COMPLETE. All requested dftracer features verified present in the trace.**

Build: Makefile (not the CMakeLists), CPU-only MPI+OpenMP, **no proj / HDF5 /
FFTW / ZFP / SZ** — SW4 needs only MPI + LAPACK. `-lsci_cray_mp` must be linked
explicitly because `mpicc`/`mpic++` from the Cray MPICH bin dir do NOT auto-link
libsci (only the `cc`/`CC`/`ftn` craype wrappers do). CCE links Fortran objects
into an `mpic++` executable with no extra flags. GPU/RAJA/HIP build not
attempted — SW4's RAJA path needs a separate dependency stack and the CPU build
was clean immediately.

dftracer had to be installed from the **LC GitLab** remote: the GitHub `develop`
build installs green and reports `features=[papi,variorum,...]` but the
installed `dftracer_config.hpp` has NEITHER `DFTRACER_PAPI_TRACING_ENABLE` nor
`DFTRACER_VARIORUM_ENABLE` defined. The GitLab build does; verified at artifact
level plus the build log line `variorum power domains to build: AMD_GPU`.
See [[feedback-never-prebuilt-when-config-knobs-needed]].

**Measured baseline** (2 nodes x 16 ranks x 11 OMP threads, SCEC LOH.1 coarsened
to h=75 over 30000x30000x17000 m = 36.7M grid points, t=9.0 s, 715 timesteps,
rc=0, 89 s wall): **1,155,012 events**, 18 trace files, all gzip-intact, 16/16
ranks `end`-terminated.
App (16 files, 1,097,296): CPP_APP 916,610 · p2p 94,868 · STDIO 37,218 ·
env 28,186 · POSIX 6,690 · comm 3,596 · collective 2,208 · papi 1,408 ·
datatype 720 · topology 64.
Service (2 files, 57,716): sys 36,472 · io 19,928 · net 1,128 · gpu 188.

**PAPI landed exactly**: all 4 requested presets
(`PAPI_TOT_CYC,PAPI_TOT_INS,PAPI_FP_OPS,PAPI_FP_INS`) present with
`args.multiplex == 0` — exact hardware counts, no time-sharing. Note the same
request on a LOGIN node warned about multiplexing; multiplex must always be read
from the compute-node run, never from a probe.

**Variorum**: 188 `type == 13` records, `cat == "gpu"`, real per-socket watt
values on 4 MI300A sockets. This is NODE POWER TELEMETRY on a CPU-only build —
it is NOT GPU kernel tracing, and must not be reported as GPU events present.
`KERNEL_DISPATCH` / `MEMORY_COPY` / `HIP_RUNTIME_API` are correctly absent
(`DFTRACER_HIP_TRACING_ENABLE` undefined, CPU build).

**Three annotator defects found**, all detailed in [[workload-sw4]]:
1. SW4's C++ files use the `.C` extension. `source_parser._try_clang` computes
   `lang = "c" if suffix.lower() == ".c" else "c++"`, so `.C` is parsed as C and
   every file fails. Worked around by renaming `.C` -> `.cpp` in the annotated
   tree plus 4 Makefile references. The real fix belongs in the tool.
2. `#include <dftracer/dftracer.h>` is inserted after the file's LAST `#include`
   — in `EW.C` that is line 8601 of 8683, while first macro use is line 543.
3. `DFTRACER_CPP_INIT` was anchored below three early exits of `main` that
   already carried `REGION_END` + `FINI`, i.e. finalize-before-initialize.

Also reproduced [[bug-clang-add-braces-multiline-call-corruption]] on 2 files
(`fastmarching`, `sacsubc`). Repaired deterministically by diffing
annotated-minus-DFTRACER-lines against pristine and deleting the leftover
inserted brace lines — no re-annotation needed. In C++ RAII mode there are no
`FUNCTION_END` macros, so the brace pass is unnecessary for SW4 anyway.

**One function had to be excluded**: `EW::getDepth` is called per grid point and
alone produced 97% of app events (554k of 568k) on a trivial test grid, at 2.8x
wall-clock. With it excluded, overhead is ~16% and the trace is well balanced.
Nothing else needed exclusion.

Coverage: 112 of 121 C++ files annotated, 1059 spans, 1059 `comp=` UPDATEs, 0
lint violations. The 9 skipped are 3 all-`#ifdef USE_HDF5` files and 6
below-cost-threshold files. **The 25 Fortran-77 files (23 QUADPACK + 2 analytic
test solutions) are NOT instrumented** — the clang annotator has no Fortran path.
They are quadrature/analytic-solution support code, not the solver.

Correctness validated: pristine, annotated with `DFTRACER_ENABLE=0`, and fully
traced all produced byte-identical stdout and receiver output files.

**Why:** requested as annotate + build + 2-node smoke run capturing the maximum
dftracer feature set, then PROVE from the trace which event types are present.

**How to apply:** load [[workload-sw4]] first — it has the minimal `make.inc`,
the `#pragma ivdep` build break, the `.C` rename recipe, the `getDepth`
exclusion, and the validated run-sizing table. [[system-tuolumne]] has the PAPI
7.2.0.2 pin. For the service daemon see
[[bug-dftracer-service-start-blocks-flux-run]] and
[[bug-dftracer-service-hosts-must-be-pinned]] — running the allocation at
exactly the job size (2 nodes) plus host-pinning gave non-empty per-node service
traces on the first attempt.

Not done: dfanalyzer analysis, diagnosis, the 4-dimension optimization loop, and
`final_report/`. This session stopped at a verified full-feature baseline.
