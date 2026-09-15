---
name: project-xsbench-fullfeature-dftracer-baseline
description: XSBench openmp-threading (MPI+OpenMP CPU) annotate + full-feature dftracer 2-node baseline on Tuolumne — complete; PAPI multiplex=0 exact, variorum GPU power + node counters captured; found the hot-loop annotation trap and the GitHub-develop silent PAPI/variorum drop
metadata:
  type: project
---

Annotated XSBench (ANL-CESAR Monte Carlo cross-section lookup proxy app) with
dftracer FUNCTION-mode source annotation, built it, and ran a 2-node
full-feature smoke trace on Tuolumne. **Complete.**

**Variant: `openmp-threading` (C, `make MPI=yes`)** — the only variant whose
Makefile has an MPI switch. The `hip` variant never defines `-DMPI` or links
MPI, so it was not a low-effort GPU alternative.

**Run:** 2 nodes x 4 ranks x 22 OpenMP threads, `-m event -s large
-l 200000000 -G unionized`, 71 s wall, 32.3 M lookups/s. 110,151 events over
8 per-rank + 2 per-node traces.

**Verified inventory:** app `C_APP` 79, `STDIO` 33,004, `POSIX` 11,312,
`papi` 5,512 (type 11), `collective` 16 (`MPI_Barrier`+`MPI_Reduce` only);
service `sys` 28,712, `io` 15,688, `net` 888 (type 7), `gpu` power 148
(type 13, Variorum, per-socket watts). **`multiplex: 0` on all PAPI records** —
all 4 requested counters landed exactly. No GPU kernel events (CPU build).
Variorum cpu/memory/network/node domains absent: no E-SMI on the build machine,
so only `AMD_GPU` was compiled in.

**Three reusable findings:**

1. **GitHub `develop` dftracer silently drops PAPI and Variorum.** CMake
   discards both flags as `unused-cli`, the build goes green, and
   `session_install_dftracer` still reports
   `features=[mpi,papi,variorum,hwloc]` — it reports what was *requested*. The
   installed `dftracer_config.hpp` had no `DFTRACER_PAPI_TRACING_ENABLE` /
   `DFTRACER_VARIORUM_ENABLE` line at all. Reinstalling from czgitlab fixed it.
   Recorded in [[tools-dftracer]]; see
   [[feedback-never-prebuilt-when-config-knobs-needed]].

2. **The annotator instrumented XSBench's per-lookup kernels.** A config that
   ran in ~18 s did not finish in 380 s annotated **even with
   `DFTRACER_ENABLE=0`**. Fixed by re-annotating `Simulation.c` with
   `exclude_functions` for `calculate_micro_xs`, `calculate_macro_xs`,
   `grid_search`, `grid_search_nuclide`, `pick_mat`, `fast_forward_LCG`.
   Recorded in [[workload-xsbench]]; same class as
   [[bug-clang-annotator-silent-zero-functions]].

3. **`dftracer_index` reporting `Events processed: 0` breaks `view`, not just
   `stats`.** With an empty bloom filter every chunk is pruned
   (`Chunks: scanned=0 skipped=10 | Events: matched=0`), exit 0, empty output —
   indistinguishable from "category absent". `dftracer_reader` is the working
   escape hatch (1-based lines; returns 0 lines if `end` exceeds EOF, so bisect
   the count first). Recorded in [[dftracer-trace-utils]]; extends
   [[bug-dftracer-stats-categories-zero-events]].

**Why:** requested as annotate + build + 2-node maximum-feature smoke trace with
the event inventory PROVEN from the trace rather than assumed.

**How to apply:** build recipe, hot-loop exclusion list, run sizing
(~4.5 M lookups/s/rank) and the `INVALID CHECKSUM` non-issue are in
[[workload-xsbench]]. Two habits generalize: check the *installed header*
rather than the install tool's feature list, and prove an annotation is
correctness-preserving by running the unmodified binary at identical arguments
and comparing the app's own checksum (both gave 437675). Not done: no
optimization pass, no final_report.
