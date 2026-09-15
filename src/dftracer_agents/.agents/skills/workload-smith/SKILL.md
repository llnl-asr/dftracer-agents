---
name: workload-smith
description: Build/annotate/trace caveats for Smith (github.com/LLNL/smith), the LLNL 3D implicit nonlinear thermal-structural FEM code (the renamed Serac). Covers the inaccessible prebuilt TPL stack, the minimum from-source dependency chain, four upstream source bugs that block a from-scratch build, the fact that the `smith` driver target is disabled upstream, and which binary to use as a run/smoke target. Load this skill for any Smith or Serac session.
---

# Smith (LLNL) — build, annotate, trace

Smith is a **3D implicit nonlinear thermal-structural simulation code** built on
**MFEM**, formerly named Serac (`LLNL-CODE-805541`, still `readthedocs/serac`,
`codecov/serac`). C++20, CMake + **BLT**, MPI required. Pre-alpha: interfaces
churn, and some targets in `develop` do not compile.

## RULE 1 — the prebuilt TPL stack is group-gated; plan to build it yourself

`host-configs/<system>-*.cmake` point every TPL at a shared workspace tree owned by a
restricted project group (mode `drwxrws---`). If you are not in that group the
whole stack is `Permission denied` and **every** host-config is unusable as-is.
The host-config is still valuable — read it for the *validated toolchain* and
TPL version pins, then rebuild those TPLs into the session workspace.

## RULE 2 — use `SMITH_ENABLE_CODEVELOP=ON`; it collapses the dependency stack

Codevelop builds **MFEM, Axom, Tribol, gretl and ContinuationSolvers from the
in-tree submodules** in one CMake project. That reduces the from-source TPL list
to a tractable set:

| TPL | why | notes |
| --- | --- | --- |
| METIS 5.1.0 | MFEM (`MFEM_USE_METIS_5` is hardcoded ON) | `github.com/mfem/tpls` mirror; `make config cc=$CC` |
| ParMETIS 4.0.3 | `PARMETIS_DIR` is a hard `smith_assert_is_directory` | same mirror |
| hypre | MFEM | builds in ~90 s |
| HDF5 | Conduit (serial HDF5 is sufficient) | build from source, never the Cray module |
| camp | hard `FATAL_ERROR` if `CAMP_DIR` unset | needs `-DBLT_SOURCE_DIR=<smith>/cmake/blt` |
| Conduit | hard `FATAL_ERROR` if `CONDUIT_DIR` unset | BLT lives at `<conduit>/src/blt`, **not** `<conduit>/blt` |
| SuperLU_DIST | **not optional** — see RULE 4 | needs ParMETIS + BLAS/LAPACK |

Umpire, RAJA, Sundials, PETSc, Caliper, Adiak, Enzyme, NetCDF are genuinely
optional. STRUMPACK is *nearly* optional — see RULE 5.

Initialise submodules first (`axom` alone is ~277 MB).

## RULE 3 — Smith needs a C++20 `<format>`, i.e. a modern libstdc++

Smith uses `std::format`. Clang picks up the system libstdc++, which on TOSS4 is
too old. Point the compiler at a GCC 13+ toolset:
`--gcc-toolchain=/opt/rh/gcc-toolset-13/root/usr` in `CMAKE_C_FLAGS` and
`CMAKE_CXX_FLAGS`, plus `-Wl,-rpath,<toolset>/lib/gcc/x86_64-redhat-linux/13`.

**Upstream bug:** ~33 Smith sources use `std::format` **without including
`<format>`** (e.g. `src/smith/infrastructure/logger.cpp`). libstdc++ 13 does not
pull it in transitively, so the build dies at `no member named 'format' in
namespace 'std'`. Fix it *scoped to Smith's own targets* so you do not invalidate
the ccache for MFEM/Axom/Tribol — add to `src/CMakeLists.txt` and
`examples/CMakeLists.txt`:

```cmake
add_compile_options($<$<COMPILE_LANGUAGE:CXX>:-include$<SEMICOLON>format>)
```

## RULE 4 — SuperLU_DIST is a hard requirement, not an option

`src/smith/numerics/equation_solver.hpp` declares `smith::SuperLUSolver` with
`mfem::SuperLURowLocMatrix` / `mfem::SuperLUSolver` members **outside any
`#ifdef MFEM_USE_SUPERLU`** (unlike the STRUMPACK block right below it, which is
guarded). Without SuperLU_DIST, `smith_numerics` cannot compile.

Because Smith sets `set(MFEM_USE_SUPERLU ON CACHE BOOL "")` — a *cache* set — it
cannot flip an already-cached `OFF`. **Adding `SUPERLUDIST_DIR` to an existing
build dir silently does nothing**; you must configure a fresh build directory.

## RULE 5 — one unguarded STRUMPACK use blocks every example

`src/smith/physics/solid_mechanics_contact.hpp` constructs a `StrumpackSolver`
for the AMGF filtered-subspace solver with no `#ifdef MFEM_USE_STRUMPACK`. It is
header code, so it breaks **all** examples even ones with no contact. Either
build STRUMPACK (needs ScaLAPACK, ~1 h) or guard it. `filter_solver_` is a
`std::unique_ptr<mfem::Solver>`, so SuperLU_DIST substitutes cleanly:

```cpp
#ifdef MFEM_USE_STRUMPACK
      filter_solver_ = std::make_unique<StrumpackSolver>(filter_solver_print_level, comm);
#else
      filter_solver_ = std::make_unique<SuperLUSolver>(filter_solver_print_level, comm);
#endif
```

## RULE 6 — there is NO `smith` driver binary; run an example

`src/drivers/CMakeLists.txt` has its `smith_add_executable(... smith_driver ...)`
**commented out upstream**. Uncommenting it does not help: `src/drivers/smith.cpp`
is stale against the current API and fails with 12 × *"no matching constructor
for initialization of `smith::SolidMechanics<p,dim>` / `HeatTransfer<p,dim>`"*.
Do not spend time on it.

Runnable binaries are the **examples** (`build/examples/`):
`composable_thermo_mechanics`, `composable_thermo_mechanics_advanced`,
`composable_solid_mechanics`, `simple_conduction`, `buckling_cylinder`, plus
`bin/partitioner`. `SMITH_ENABLE_BENCHMARKS=ON` does **not** work — BLT cannot
find a gbenchmark submodule and configure fails.

**Best trace/smoke target: `composable_thermo_mechanics`** — a coupled 3D
thermo-mechanics run that matches Smith's actual purpose. It takes no CLI
arguments; size it *in source*:

```cpp
mfem::Mesh::MakeCartesian3D(80, 20, 20, mfem::Element::HEXAHEDRON, 1.0, 0.1, 0.1)  // was 8,2,2
for (int step = 0; step < 5; ++step)                                               // was 2
```
That is ~81 s on 2 nodes × 16 ranks (CPU, MPI-only), a good 1-3 min smoke size.

## RULE 7 — annotate the EXAMPLE's `main`, not just `src/`

The examples are the entry points, so if you scope annotation to `src/` only,
**no `DFTRACER_CPP_INIT`/`FINI` is emitted anywhere** and every trace file is
written **0 bytes** with the app still exiting `rc=0`. This is silent. Put
`DFTRACER_CPP_INIT` / `REGION_START` immediately **after** the
`smith::ApplicationManager` constructor (that is what calls `MPI_Init`) and
`REGION_END` → `FINI` before the final `return`.

**Annotator trap seen here:** in an entry file the tool inserted
`REGION_END` + `FINI` before a `SLIC_ERROR_ROOT_IF(...)` — a *conditionally*
aborting macro, not a return. Execution continues past it, so the tracer was
finalised before the whole simulation. Always read `main` top-to-bottom and
delete any END/FINI pair that is not immediately before a `return`/`exit`.

## RULE 8 — compile flags for the clang annotator

Smith sources use quoted project includes (`#include "smith/physics/mesh.hpp"`),
so the annotator needs **`-I <tree>/src`** (the compile-DB entry
`-I<tree>/src/smith/infrastructure/../..` resolves to `<tree>/src/smith` and is
*not* enough). Also add `-isystem <tree>/gretl/src` and the dftracer include dir.
Without them clang emits a fatal "file not found", the brace-insertion safety
pass self-disables, and functions get mis-reported as "trivial". Extract the rest
of the flags from `compile_commands.json` (`CMAKE_EXPORT_COMPILE_COMMANDS=ON`)
rather than hand-writing them — Axom/MFEM/Conduit contribute ~20 `-isystem` paths.

Parsing one Smith `.cpp` costs minutes because of the Axom+MFEM header mass;
expect client-side MCP timeouts and always grep the file before retrying.

## RULE 9 — measured tracing cost, and the hot accessor

2 nodes × 16 ranks, 80×20×20 hexes, 5 steps, full-feature FUNCTION-mode tracing:

| binary | wall |
| --- | --- |
| pristine | 81 s |
| annotated, `DFTRACER_ENABLE=0` | 82 s (instrumentation ≈ free) |
| annotated, tracing on | 201 s (**2.5×**) |

The overhead is concentrated in one per-element accessor: `GetElementVDofs`
(`numerics/functional/element_restriction.cpp`) was ~61 % of all app events on
rank 0. Pass it to `clang_annotate_file(exclude_functions=[...])` if you need
low-overhead traces.

## Event inventory actually obtained (2 nodes x 16 ranks, CPU MPI build)

32 app traces = **21,600,641** events; 2 service traces = **48,645** events.

| layer | cat | events | note |
| --- | --- | ---: | --- |
| MPI point-to-point | `p2p` | 14,950,641 | hypre CG halo exchange |
| MPI environment | `env` | 5,625,383 | comm/rank/type queries |
| MPI communicator ops | `comm` | 494,130 | |
| MPI collectives | `collective` | 16,518 | |
| STDIO | `STDIO` | 179,928 | |
| **App annotation** | `CPP_APP` (type 9) | 166,042 | 83 distinct functions fired |
| POSIX | `POSIX` | 121,791 | |
| dftracer metadata | `dftracer` (type 1) | 38,393 | |
| PAPI counters | `papi` (type 11) | 7,815 | |
| node system | `sys` (type 7) | 40,158 | service traces |
| node I/O | `io` (type 7) | 7,038 | service traces |
| node network | `net` (type 7) | 1,242 | service traces |
| **variorum power** | `gpu`, **type 13** | 207 | service traces |

GPU kernel-dispatch / memory-copy events are legitimately **absent** — this is a
CPU-only build with `DFTRACER_HIP_TRACING_ENABLE` undefined. Note that variorum
still reports a `gpu` power category: that is node-level power telemetry, not GPU
kernel tracing.

PAPI: `PAPI_TOT_CYC,PAPI_TOT_INS,PAPI_FP_OPS,PAPI_FP_INS` all landed, every
sample with `args.multiplex == 0` (**exact**, not scaled) — 4 counters against
this node's 5-counter budget. Read `multiplex` from the compute-node run; a
login-node probe can report a different value.

Annotation liveness: **83 of 249** instrumented sites fired. The unfired ones are
code paths this example does not exercise — Lua/Inlet input parsing
(`*_input.cpp`, `cli.cpp`), Tribol contact, PETSc solvers, trust-region solvers,
mesh-file readers and `StateManager` output (the example builds its problem
programmatically and writes no output).

Reading the traces: `dftracer_stats --report categories` hits the known
"Events Scanned: 0" bug. Use `dftracer_reader --mode lines` and aggregate the
NDJSON. Two schema traps: **`type` is a top-level field**, not under `args`, and
reader output lines may carry a leading `[`/`,` or a trailing `,` — strip them or
a naive `line.startswith('{')` filter silently drops ~50% of the events.

## Related

[[system-tuolumne]] [[software-cmake]] [[software-papi]] [[dftracer-annotate-cpp]]
[[dftracer-trace-utils]] [[workload-laghos]] (also MFEM-based)
