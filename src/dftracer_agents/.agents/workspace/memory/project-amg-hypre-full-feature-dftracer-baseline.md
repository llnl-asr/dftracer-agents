---
name: project-amg-hypre-full-feature-dftracer-baseline
description: AMG (hypre proxy, C/MPI+OpenMP) annotate + 2-node full-feature dftracer baseline on Tuolumne — 1.32M events with EXACT PAPI (multiplex=0), variorum GPU power and node counters; github dftracer develop silently lacks PAPI/variorum
metadata:
  type: project
---

AMG (`LLNL/AMG`, hypre-based algebraic-multigrid proxy, ISO-C, MPI+OpenMP, plain
Makefile) annotated with dftracer FUNCTION mode and baseline-traced on Tuolumne
MI300A with PAPI hardware counters, Variorum power and the per-node
`dftracer_service` daemon.

**Status: COMPLETE. Build, annotation, 2-node run and event inventory all verified
against real artifacts.** Full detail is in the new [[workload-amg]] skill — load
that, not this entry, to actually do the work.

**The single most important finding, and it is NOT AMG-specific:**
`github.com/llnl/dftracer` `develop` (v2.0.3, `70bf822`) has **no PAPI and no
Variorum support at all**. `session_install_dftracer(papi=True, variorum=True)`
completes with `exit: 0` and looks entirely healthy; cmake merely lists
`DFTRACER_ENABLE_PAPI_TRACING` and `DFTRACER_ENABLE_VARIORUM` under
`CMake Warning (unused-cli): Manually-specified variables were not used by the
project` and moves on. The installed `dftracer_config.hpp` then contains neither
`DFTRACER_PAPI_TRACING_ENABLE` nor `DFTRACER_VARIORUM_ENABLE`. Those features live
only on the LC-internal remote:

```
dftracer_repo="https://github.com/llnl-asr/dftracer.git"
dftracer_ref="develop"          # 81894ce
```

Verify at artifact level every time, never from the install's exit status:
`DFTRACER_PAPI_TRACING_ENABLE 1`, `DFTRACER_VARIORUM_ENABLE 1`,
`DFTRACER_PAPI_HW_COUNTERS 5`, `DFTRACER_PAPI_FITTING_COUNTERS 7`, and the build-log
line `[DFTRACER] variorum power domains to build: AMD_GPU`. Reinforces
[[feedback-never-prebuilt-when-config-knobs-needed]].

**The variorum-linkage fix is present on that branch** — `ldd` shows
`libvariorum.so` linked into `dftracer_service` only, never into
`libdftracer_core.so`, and only ONE `librocm_smi64` soname reaches the app. No
repeat of [[bug-dftracer-variorum-linked-into-app-heap-corruption]].

**Measured baseline** (8 ranks = 2 nodes x 4, `-P 2 2 2 -n 100 100 100 -problem 1`,
13 s, rc=0, all 8 ranks flushed a trailing `end`): **1,318,447 events** —
C_APP 1,023,178 / p2p 195,417 / STDIO 33,004 / comm 25,358 / POSIX 11,312 /
env 1,841 / collective 1,507 / papi 208, plus per-node service counters
(sys 9,700, io 1,700, net 300) and **50 Variorum `type 13` `cat=gpu` power records**
carrying four `socket_N.GPU_N` wattages each. GPU KERNEL_DISPATCH/MEMORY_COPY are
correctly absent — AMG is CPU-only and was built `hip=False`.

**PAPI came out EXACT** — `PAPI_TOT_CYC,PAPI_TOT_INS,PAPI_FP_OPS,PAPI_FP_INS` all
landed with `multiplex=0` on a 5-hardware-counter node even though `PAPI_FP_INS` is
derived. Worth knowing as a ready-made exact set for MI300A ([[software-papi]]).

**Three tool defects found, each of which produced a plausible-looking wrong result:**

1. `clang_annotate_project` / `clang_annotate_file` do not forward `compile_flags` to
   their brace-insertion pass. The pass self-disables ("refusing to trust a degraded
   AST") while function annotation proceeds, so `DFTRACER_C_FUNCTION_END()` gets
   inserted between a braceless `if` and its body and the guarded statement becomes
   unconditional. 22 such sites; most COMPILE CLEANLY. Extends
   [[bug-clang-add-braces-overlap-corruption]] and
   [[bug-clang-add-braces-multiline-call-corruption]] with a third shape: the macro
   separated from the control head by a blank line. Without the flags the annotator
   also finds only 251 functions instead of 388, still reporting `status: ok`.
2. The same annotator inserts `#include <dftracer/dftracer.h>` after the LAST
   `#include` anywhere in the file — including commented-out ones thousands of lines
   down — landing the include below most annotated functions.
3. `session_patch_build` emits autotools `AM_*` variables for `build_tool="make"`,
   which a hand-written Makefile never reads: a silent no-op.

**And the whole MCP trace-utils read path was unusable on these traces.**
`dftracer_info` reports `Valid Events: 0` against `Total Lines: 1318447`, so the
bloom indices are empty and every `dftracer_view` query returns
`Chunks: scanned=0 skipped=10 | Events: matched=0` with exit 0 — for EVERY query
shape, not just `!=`. This is [[bug-dftracer-stats-categories-zero-events]] biting
`view` as well as `stats`, and it is the reason `mcp__dftracer__split` also produced
nothing. The workable path is `dftracer_reader --mode lines` per file (it decodes
correctly and its line total matches `event_count` exactly) with aggregation over its
NDJSON output. Note the traces encode `ph` as an INTEGER (1/2/4), and `type` is a
TOP-LEVEL field, not inside `args` — counting `args.type` silently finds zero
Variorum records.

**Why:** requested as "annotate AMG, build it, run a 2-node smoke test capturing the
maximum set of dftracer features, then PROVE from the trace which event types are
actually present."

**How to apply:** load [[workload-amg]] first — it carries the missing
`HYPRE_BoomerAMGGetCumNnzAP` prototype that breaks the pristine build on clang >= 15,
the `Makefile.include` integration point, the `hypre_MPI_Init`/`hypre_MPI_Finalize`
wrapper problem that puts INIT/FINI on the wrong side of MPI, the hot-function
exclusion list (`hypre_qsort2abs` alone is 72% of all C_APP events), and the run
sizing table. [[system-tuolumne]] has the `papi/7.2.0.2` pin and the
`dftracer_service` host-pinning and core-reservation rules.

Not done: dfanalyzer analysis, diagnosis, optimization loop, final_report. This
session stopped at a verified baseline with a proven event inventory.
