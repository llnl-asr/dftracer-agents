---
name: project-minivite-fullfeature-dftracer-baseline
description: miniVite (ECP-ExaGraph Louvain proxy) annotate + 2-node full-feature dftracer baseline — complete: 17 header-resident functions annotated, PAPI EXACT (multiplex=0), variorum power, node counters, 98,010 events, annotation proven correctness-preserving
metadata:
  type: project
---

miniVite (`ECP-ExaGraph/miniVite`) — C++11 MPI+OpenMP distributed Louvain
community-detection proxy app, plain Makefile — annotated in dftracer FUNCTION
mode, built, and traced with the MAXIMUM feature set on 2 nodes of an MI300A /
Cray PE / Flux system. **Complete; every claim verified against a real file or
trace.**

**Result:** rc=0, 73 s, 16/16 ranks flushed (matched `start`/`end`, zero 0-byte
traces), **98,010 events** across 16 app + 2 service traces. All layers present
except GPU, which is legitimately absent (CPU-only app, `hip=False`).

Layer counts: `CPP_APP` 1,184 (17 functions) / `POSIX` 4,176 / `STDIO` 29,292 /
`p2p` 7,580 / `collective` 1,280 / `comm`+`datatype`+`env` 272 / `papi` 10,112 /
`sys` 32,204 + `io` 5,644 + `net` 996 (node counters) / `gpu` type-13 166
(variorum power) / `dftracer` 5,104 incl. 7 app-metadata keys x 16 ranks.

**PAPI was EXACT** — `multiplex == 0` on all 10,112 records and all four
requested counters landed, even though one is a derived preset and the node has
5 hardware counters. No PAPI teardown SIGSEGV on this build.

**Three findings worth carrying forward, all now in [[workload-minivite]]:**

1. **`clang_annotate_project` annotates NOTHING useful on this app.** All the
   work lives in three `.hpp` files; the tool only scans `.c/.cpp/.cxx/.cc`, so
   it instruments `main.cpp` alone and returns green. The headers must be
   annotated explicitly via `clang_annotate_file`, and they need
   `-include mpi.h` because they use `MPI_Datatype` without including `<mpi.h>`
   themselves. Same failure *shape* as
   [[bug-clang-annotator-silent-zero-functions]], different cause.
2. **The synthetic RGG generator, not Louvain, dominates wall time** — it is
   O((nv/p)^2). At `-n 2097152` on 16 ranks: 63.1 s graph generation vs 0.18 s
   solve. Sizing an `-n` run by wall time and attributing it to community
   detection is wrong; use a real `-f` input graph to study Louvain.
3. **Per-vertex helpers must be excluded** (`distExecuteLouvainIteration`,
   `distGetMaxIndex`, `distBuildLocalMapCounter`) — they are called once per
   local vertex per iteration from every OpenMP thread.

**Annotation proven correctness-preserving:** pristine, annotated-with-tracing-
off, and fully-traced all produced modularity 0.75311 / 20 iterations exactly.

**Tool gap found:** `compile_flags` reach `clang_extract_functions` but not the
internal `clang_add_braces` pass, which then self-disables with a warning while
annotation proceeds. Harmless in C++ RAII mode (no `END` macros are emitted at
all), but a real hazard in C mode.

**Install note (cost two installs):** `github.com/llnl-asr/dftracer@develop` builds
green and reports success while CMake silently discards
`DFTRACER_ENABLE_PAPI_TRACING` and `DFTRACER_ENABLE_VARIORUM` as "unused" — those
options only exist on the czgitlab remote. Verify the installed
`dftracer_config.hpp`, never the install status.

**Why:** requested as a maximum-feature dftracer baseline that PROVES from the
trace which event types are present, rather than asserting them.

**How to apply:** load [[workload-minivite]] before any miniVite work — it
carries the exact compile flags, the exclusion list, the run-sizing table, and
the measured event inventory. Related:
[[feedback-never-prebuilt-when-config-knobs-needed]], [[tools-dftracer]],
[[software-papi]], [[system-tuolumne]],
[[bug-dftracer-service-hosts-must-be-pinned]],
[[feedback-dftracer-service-node-counters]],
[[bug-dftracer-service-start-blocks-flux-run]],
[[feedback-always-function-mode]].
