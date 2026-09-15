---
name: project-smith-serac-cpu-dftracer-baseline
description: Smith (LLNL, renamed Serac) MFEM thermal-structural code — full from-source TPL stack + annotate + 2-node full-feature dftracer baseline on Tuolumne; 4 upstream source bugs found, driver target is dead upstream
metadata:
  type: project
---

Smith (`github.com/LLNL/smith`, branch `develop`) is LLNL's 3D **implicit nonlinear thermal-structural FEM** code built on MFEM — the renamed **Serac** (`LLNL-CODE-805541`; docs/badges still say serac). C++20, CMake + BLT, MPI required. All app-specific detail lives in the **[[workload-smith]]** skill; this entry is the session-state pointer.

**Outcome: COMPLETE.** Built from scratch, annotated, ran a 2-node full-feature dftracer smoke test (rc=0, 201 s), and proved the event inventory from the traces.

**Why it was work:** every `host-configs/*.cmake` points its TPLs at a group-gated prebuilt tree owned by a restricted project group (mode `drwxrws---`). Without that group the whole stack is unreadable, so the TPLs had to be rebuilt from source. `SMITH_ENABLE_CODEVELOP=ON` was the unlock — it builds MFEM, Axom, Tribol, gretl and ContinuationSolvers from the in-tree submodules, cutting the external list to METIS, ParMETIS, hypre, HDF5, camp, Conduit, SuperLU_DIST.

**Four upstream defects that block a from-scratch build** (all detailed with fixes in [[workload-smith]]):
1. ~33 sources use `std::format` without `#include <format>` (libstdc++ 13 doesn't pull it in transitively).
2. `equation_solver.hpp` uses `mfem::SuperLUSolver` **unguarded**, so SuperLU_DIST is mandatory, not optional.
3. `solid_mechanics_contact.hpp` constructs a `StrumpackSolver` with no `#ifdef MFEM_USE_STRUMPACK` — header code, so it breaks *every* example.
4. The `smith` driver target is **commented out upstream** and `src/drivers/smith.cpp` is stale against the current `SolidMechanics`/`HeatTransfer` constructors. There is no `smith` binary; the **examples** are the runnable app. Trace target used: `composable_thermo_mechanics`, resized in source.

**The silent failure worth remembering:** scoping annotation to `src/` only means the example's `main` gets **no `DFTRACER_CPP_INIT`/`FINI`**, and every trace file is written **0 bytes while the app exits rc=0**. Also caught the annotator inserting `REGION_END`+`FINI` before a *conditionally* aborting `SLIC_ERROR_ROOT_IF` in an entry file — not a return, so the tracer was finalised before the whole simulation.

**Measured (2 nodes x 16 ranks, 80x20x20 hexes, 5 steps):** pristine 81 s; annotated with `DFTRACER_ENABLE=0` 82 s (instrumentation is essentially free); annotated with full tracing 201 s (2.5x). Overhead concentrates in one per-element accessor, `GetElementVDofs` (~61% of app events) — exclude it for low-overhead traces.

**Verified present in the trace:** MPI (`p2p`, `comm`, `collective`, `env`), `POSIX`, `STDIO`, `CPP_APP` app annotation (83 distinct instrumented functions fired of 249 sites), `papi` with all four requested counters and `args.multiplex == 0` (exact), and per-node service traces with `sys`/`io`/`net` plus variorum `type 13` power. GPU kernel events legitimately absent (CPU-only build).

**Tooling note that cost real time:** the public GitHub dftracer `develop` builds *green* with `DFTRACER_ENABLE_PAPI_TRACING=ON` and `DFTRACER_ENABLE_VARIORUM=ON` but cmake reports both as unused and the installed `dftracer_config.hpp` has **neither**. Reinstalling from the LC-internal GitLab remote fixed it — reconfirms [[feedback-never-prebuilt-when-config-knobs-needed]]. Always grep the installed header.

See also [[workload-laghos]] (the other MFEM-based workload), [[system-tuolumne]], [[software-papi]].
