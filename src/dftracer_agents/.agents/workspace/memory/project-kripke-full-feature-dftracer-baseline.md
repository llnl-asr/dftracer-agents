---
name: project-kripke-full-feature-dftracer-baseline
description: Kripke (RAJA Sn transport proxy) annotate + build + 2-node full-feature dftracer baseline on Tuolumne — complete; CPP_APP/POSIX/STDIO/MPI/PAPI(exact)/variorum power all proven present from the trace
metadata:
  type: project
---

Kripke 1.2.5-dev (LLNL, deterministic Sn transport, C++17 + RAJA/Umpire/CHAI,
BLT/CMake) annotated with dftracer FUNCTION-mode source annotation, built, and
traced at 2 nodes x 4 ranks on an MI300A system. Detail lives in the
[[workload-kripke]] skill; this is the state pointer.

**Outcome:** complete. Build variant CPU **MPI+OpenMP** (not HIP — the GPU path
needs `ENABLE_CHAI=On` plus a GPU RAJA/Umpire/CHAI compile, which is not the
"comes up cleanly with little effort" case). Run: 2 nodes, 8 ranks, 23
cores/rank, `--zones 96,96,96 --niter 15 --procs 2,2,2`, rc=0, 46 s wall,
converged. 8/8 rank traces, all gzip-intact and `end`-terminated, 928,880
events; 2/2 non-empty per-node service traces.

**Event inventory proven from the trace** (via `dftracer_reader --mode lines`):
CPP_APP 679,852 · MPI p2p 164,497 (`MPI_Testany` 158,617 / `Irecv` / `Isend` /
`Waitall`) · STDIO 39,857 · POSIX 17,604 · comm 8,088 · papi 2,905 ·
collective 400 (`MPI_Allreduce` 272, `MPI_Scan` 128). Service traces per node:
`sys` 11,834, `io` 2,074-6,466, `net` 366, and variorum **type-13 power**
records under `cat="gpu"` (61/node, real per-socket watt readings). GPU
KERNEL_DISPATCH / MEMORY_COPY absent — expected, CPU build.

**PAPI:** requested `PAPI_TOT_CYC,PAPI_TOT_INS,PAPI_FP_OPS,PAPI_FP_INS`; all
four landed and `multiplex == 0` (EXACT) on the compute node. The same set
probed on a login node reported `multiplex == 1` — see [[software-papi]] Rule 5.

**Two things that would have silently produced a bad result:**
1. GitHub `develop` dftracer has NO PAPI and NO Variorum code at all; the flags
   are accepted and ignored. czgitlab `develop` has both. Verified in the
   installed `dftracer_config.hpp` and the `variorum power domains to build:
   AMD_GPU` build line. Already recorded in [[tools-dftracer]].
2. Kripke wraps `MPI_Finalize` in `Kripke::Core::Comm::finalize()`, so the
   annotator and lint rule L3 (which match the literal symbol) both pass while
   `REGION_END`/`FINI` sit after MPI teardown. Fixed by hand.

Also: the annotator's cost filter skipped `sweepSubdomain` and the four
`SweepComm` methods — the hot kernel and the entire comm layer — because each
function body is only a timer macro plus a dispatch. Added back with
`clang_insert_line`. Correctness verified: annotated binary at
`DFTRACER_ENABLE=0` reproduces the pristine baseline's per-iteration particle
counts exactly, ~0.6% wall-time overhead.

**Why:** first full-feature (annotation + POSIX/STDIO + MPI + PAPI + variorum
power + node counters) dftracer baseline for Kripke.

**How to apply:** load [[workload-kripke]] before any Kripke work; it carries
the build recipe, the annotation gaps to repair, and the header policy. See
also [[bug-clang-annotator-silent-zero-functions]],
[[bug-dftracer-service-start-blocks-flux-run]],
[[bug-dftracer-service-hosts-must-be-pinned]], [[system-tuolumne]].
