---
name: workload-enzo
description: Build/annotate/trace caveats for Enzo (enzo-project/enzo-dev, tag enzo-2.6.1), the AMR cosmology code (C++ `.C` + Fortran, MPI). Covers the Tuolumne Make.mach recipe, the type-redefining macros that break dftracer.h, the macro-named main and my_exit() exit path, the pre-main constructors that silently disable MPI interception, rank-count limits per problem size, and measured event volumes for the ComputationalUncertaintyHPC configs.
---

# workload-enzo

Enzo (https://github.com/enzo-project/enzo-dev), ref `enzo-2.6.1`. AMR
hydro + N-body cosmology. Traced scientific workload. Problem configs used:
`victoriastodden/ComputationalUncertaintyHPC` → `<N>-<L>-10Mpc-z0/Init`
(param file `gas_plus_dm_amr_multiphys.enzo`; ICs are HDF5:
GridDensity/GridVelocities/ParticlePositions/ParticleVelocities). Run as
`enzo.exe -d gas_plus_dm_amr_multiphys.enzo` from a copy of `Init/` placed on
the PFS (`<WS>/dataset/<run>/`).

**Language mix (src/enzo):** ~951 `.C` (C++), 9 `.c`, ~150 Fortran (`.F`,
`.F90`), plus `uuid/` C. All MPI/IO/driver logic is in the `.C` files.

## Build (Tuolumne, Cray PE)

- Custom Make system, not CMake/autotools: `./configure` at the repo root,
  then in `src/enzo`: `gmake machine-tuolumne opt-aggressive use-mpi-yes grackle-no`
  then `gmake -j32`. Write `Make.mach.tuolumne` yourself: CC/CXX = Cray
  `mpicc`/`mpicxx`, FC = gcc-toolset-13 `gfortran`, rpath to HDF5 and
  libgfortran, `MACH_LIBS` carries a `$(DFTRACER_LIBS)` hook.
- HDF5 **1.10.x** from source (1.10.11 used). The configs need no Grackle
  (internal `MultiSpecies=1` cooling).
- Two Cray-clang 20 source fixes required:
  `Grid_CreateParticleTypeGrouping.C:68` `reference` → `&reference`
  (H5Dwrite arg), and `ListIO.C` `"..."__FILE__` → `"..." __FILE__` (C++11
  needs the space).
- Annotated build: pass the dftracer include via
  `gmake MACH_INCLUDES="-I$HDF5_DIR/include -I<dftracer>/include"` and link via
  `DFTRACER_LIBS="-L<dftracer>/lib64 -Wl,-rpath,<dftracer>/lib64 -ldftracer_core"`.
  Copying `source/` to `annotated/` also copies baseline `.o` files —
  `gmake clean` before the first annotated build.

## Annotation traps (all hit in practice)

1. **`.C` is C++.** `clang_annotate_project(language="cpp", cpp_extensions=[".C"])`.
   Exclude the non-solver tools: `/src/anyl/ /src/enzohop/ /src/inits/
   /src/lcaperf/ /src/mpgrafic/ /src/performance_tools/ /src/P-GroupFinder/
   /src/ring/` and `auto_show_flags.C`. Compile flags for clang must mirror the
   build's DEFINES (`make show-flags`) plus
   `-Wno-everything -Wno-c++11-narrowing -Wno-reserved-user-defined-literal`,
   which Enzo's own build relies on; otherwise headers fail to parse.
   Do not pass `-std=c++14` to the 20 `.c` files (they then fail to parse);
   they are hot deposit/interp kernels + uuid and are fine left unannotated.
2. **`macros_and_parameters.h` does `#define int long_int` and
   `#define float double`.** If `#include <dftracer/dftracer.h>` comes after
   it, dftracer's headers are mangled ("redefinition of 'long_int'", "typedef
   redefinition with different types"). dftracer.h must be the FIRST include —
   the annotator now does this by default.
3. **`main` is `Eint32 MAIN_NAME(...)` with `#define MAIN_NAME main`.** Entry
   detection must follow the macro (the annotator now does).
4. **Every exit goes through `my_exit()` → `CommunicationFinalize()` →
   `exit()`.** FINI placed at the end of `main` never runs. Put
   `DFTRACER_CPP_FINI();` inside `my_exit()` immediately before
   `CommunicationFinalize();` (enzo.C, success branch). The `main` region
   event is therefore never emitted; child functions are all traced.
5. **Global constructors run before `main`.** `ActiveParticleType` ctors
   (static particle-type registrations, 9 calls) and `StochasticForcing`
   (global `Forcing` object) are annotated by default; the first one lazily
   initializes dftracer in no-bind mode, and the later `DFTRACER_CPP_INIT` is
   a no-op. Result: zero MPI/POSIX/STDIO events, `"bind":0` in the trace's
   `end` record. Fix: re-annotate `ActiveParticleRoutines.C` and
   `StochasticForcing_constructor.C` with
   `exclude_functions=["ActiveParticleType"]` / `["StochasticForcing"]`.
6. `RadiativeTransferHealpixRoutines64.C` — annotator placed macros outside a
   one-line `{ ... }` body (HEALPix util); restore it unannotated.

Brace insertion is skipped on most files (its clang parse lacks the project
defines) — harmless for C++ since `DFTRACER_CPP_FUNCTION` is RAII.

## Runtime

- Env: `DFTRACER_ENABLE=1 DFTRACER_INIT=FUNCTION DFTRACER_INC_METADATA=1
  DFTRACER_DATA_DIR=all DFTRACER_TRACE_COMPRESSION=1`. For call-stack + MPI
  only: `DFTRACER_DISABLE_POSIX=1 DFTRACER_DISABLE_STDIO=1`.
- MPI events appear under categories `p2p`, `collective`, `comm`, `env`
  (`MPI_Wtime`), `datatype` — not a single `MPI` category.
- Smoke test: 1 process, append `StopCycle = 10` to the param file → ~0.8 s.

## Rank limits and run sizing (measured, Tuolumne, opt-aggressive)

| Config | Layout | Result |
|---|---|---|
| 16-3 (16³) | 1N x 32 | full run z=99→0, 320 cycles, 560 s, 12 RD outputs |
| 16-3 (16³) | 4N x 64 (256 ranks) | NaN "Error interpolating field 1 (x-velocity)" at cycle 109 (z≈18.9), MPI_Abort — ~16 cells/rank is too few |
| 32-7 (32³) | 2N x 32 | did NOT finish in 12 min (cycle 186, z≈5.35) |

Use 32-7 (or larger) for multi-node scaling; keep 16-3 at ≤32 ranks.
Root cause of the 256-rank NaN (over-decomposition vs opt-aggressive) not
proven; an un-annotated baseline at the same layout would settle it.

## Event volume (full annotation, function + MPI)

| Run | Events | cpp_app | p2p | collective | env | Size |
|---|---|---|---|---|---|---|
| 16-3, 1N x 32, full | 358 M | 324 M | 26.6 M | 1.4 M | 5.8 M | 3.0 GB |
| 32-7, 2N x 32, to z≈5.35 | 585 M | 539 M | 25.3 M | 6.6 M | 14.2 M | 4.9 GB |

Top names: `search_lower_bound`, `CheckForOverlap`,
`CheckForPossibleOverlapHelper`, `CommunicationShouldExit`,
`CommunicationSendRegion`, `CopyZonesFromGrid`, and on 32³
`UpdateStarParticles`/`FindNewStarParticles` (56 M each). Exclude these hot
helpers when trace size matters. Count with
`dftracer_stats -d <dir> --index-dir <idx> --report categories|top-names`.
