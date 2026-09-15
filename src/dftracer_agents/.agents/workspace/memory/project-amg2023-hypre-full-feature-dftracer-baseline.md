---
name: project-amg2023-hypre-full-feature-dftracer-baseline
description: AMG2023 + hypre 2.32.0 annotate/build/2-node full-feature dftracer baseline on Tuolumne — COMPLETE, 20.6M app events + variorum GPU power verified; 3 bugs found
metadata:
  type: project
---

AMG2023 annotated with dftracer FUNCTION mode, built against a session-local
hypre 2.32.0, traced on 2 nodes with the max feature set a CPU build supports.
Details live in the **`workload-amg2023`** skill — load that, do not re-derive.

**Outcome: complete.** 16/16 rank traces + 2/2 node-service traces, none
zero-byte, rc=0, 70.8 s at 2 nodes x 8 ranks (`-n 240^3`/rank).
20,597,216 app events (9 categories) + 40,108 service events (4 categories).

**Three bugs found, all verified against files/traces, not tool exit codes:**

1. **GitHub `develop` dftracer has NO PAPI and NO Variorum.** Install reports
   success, but both flags land in CMake's "Manually-specified variables were
   not used" warning and the installed `dftracer_config.hpp` has neither
   symbol. Reinstall from LC czgitlab
   (`ssh://git@czgitlab.llnl.gov:7999/dftracer/dftracer.git@develop`) and grep
   the header for `DFTRACER_PAPI_TRACING_ENABLE 1` / `DFTRACER_VARIORUM_ENABLE 1`.
   See [[feedback-never-prebuilt-when-config-knobs-needed]].
2. **`session_patch_build` injects `-ldftracer`, which does not exist.** The
   FUNCTION-mode library is `libdftracer_core.so`. Its pip-fallback
   `target_link_libraries` must say `-ldftracer_core` or the annotated build
   fails to link. Needs a tool-level fix.
3. **Two annotator defects on `amg.c`**: no `DFTRACER_C_INIT`/`FINI` emitted at
   all despite `main` being annotated, and `FUNCTION_END` placed AFTER
   `hypre_MPI_Finalize()`. `clang_lint_annotations` missed the latter because
   its L3 rule matches the literal `MPI_Finalize` while hypre spells it
   `hypre_MPI_Finalize` — a blind spot for EVERY MPI-wrapping library with a
   prefixed finalize. See [[bug-clang-annotator-silent-zero-functions]].

**Two measurement facts:**
- **PAPI `multiplex` is node-type dependent.** The same 4-preset request
  reported `multiplex=1` on a login node and `multiplex=0` on compute nodes.
  Read it from the trace of the run you care about. See [[software-papi]].
- **Variorum reports only the AMD GPU power domain** (type-13, `cat=gpu`,
  keys `socket_0.GPU_0`..`socket_3.GPU_3`); no CPU/RAPL, MSR denied to
  non-root. A CPU-only app still yields a `gpu` category from node power —
  that is NOT GPU kernel tracing.

**Confirmed working:** `libvariorum` links into `dftracer_service` only, not
into `libdftracer_core.so`, so
[[bug-dftracer-variorum-linked-into-app-heap-corruption]] does not reproduce.
Service start/app/stop pinned to the same hosts with allocation size == job
size, per [[bug-dftracer-service-hosts-must-be-pinned]] and
[[bug-dftracer-service-start-blocks-flux-run]]; both service traces flushed
non-empty.

**Why:** first AMG2023 session; establishes the build recipe, the very small
annotatable surface (one 3079-line C driver, 3 annotatable functions), and a
verified event inventory to compare future runs against.

**How to apply:** load `workload-amg2023` before any AMG2023 work. For any
session needing PAPI or Variorum, install from czgitlab and verify the header
— a green `session_install_dftracer` proves nothing. Always `ldd` a linked
binary for `libdftracer_core.so` before trusting the run.
