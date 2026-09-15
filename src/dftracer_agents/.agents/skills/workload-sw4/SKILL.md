---
name: workload-sw4
description: Build/annotate/trace caveats for SW4 (geodynamics/sw4), the 4th-order-accurate 3D seismic wave propagation code (C++ + Fortran-77, MPI+OpenMP). Covers the minimal Cray/Tuolumne build, the `.C`-extension annotator trap, the two annotator placement bugs it exposes, the ONE hot function that must be excluded, and validated 2-node run sizing.
---

# workload-sw4

SW4 (https://github.com/geodynamics/sw4) — "Seismic Waves, 4th order accuracy".
Finite-difference elastic/anelastic wave propagation on Cartesian and
curvilinear grids. A *traced scientific workload* (hence `workload-*`, see
`feedback-software-vs-workload-naming`).

**Language mix:** 121 `.C` (C++) + 79 `.h` + 25 `.f` (Fortran-77: 23 QUADPACK
quadrature routines + `rayleighfort.f` + `lamb_exact_numquad.f`). The Fortran is
support code for analytic test solutions and numerical quadrature — the entire
solver, I/O and MPI layer is C++.

## Build: use the Makefile, not CMake, and keep dependencies minimal

`session_detect` reports `build_tool: cmake` because a `CMakeLists.txt` exists,
but the **Makefile** is the maintained path and is far simpler. SW4 builds
cleanly with **no proj, no HDF5, no FFTW, no ZFP, no SZ** — those are all opt-in
via `configs/make.inc`. The only hard external dependency beyond MPI is
**LAPACK** (`dgetrf`, `dgetrs`, `dgesv`, `dgels`).

`configs/make.inc` is auto-included if present and overrides the hostname
dispatch block. Minimal working file on a Cray PE system:

```make
  proj = no
  hdf5 = no
  fftw = no
  zfp  = no
  sz   = no
  openmp = yes
  CXX = <mpic++>
  FC  = <ftn>
  EXTRA_LINK_FLAGS = -fopenmp -L$(CRAY_LIBSCI_PREFIX_DIR)/lib -lsci_cray_mp \
                     -Wl,-rpath,$(CRAY_LIBSCI_PREFIX_DIR)/lib
```

Notes:
- **libsci is NOT auto-linked by `mpicc`/`mpic++`** from the Cray MPICH bin
  directory (only the `cc`/`CC`/`ftn` craype wrappers auto-link it). Link
  `-lsci_cray_mp` explicitly (the `_mp` variant for an OpenMP build).
- **Mixed C++/Fortran linkage needs nothing special** with CCE: objects from
  `ftn` link straight into an `mpic++` executable, Fortran runtime included.
- Build directory is `optimize_mp/` (`optdir` + `_mp` for openmp=yes).
- `make` default is `openmp=yes prec=double`; empty `proj`/`hdf5` variables are
  correctly treated as "no".

### Expected build break on Cray clang: `#pragma ivdep` before `#pragma omp simd`

6 sites (in `GridGenerator.C` and `rhs4curvilinearc.C`) have

```c
#pragma ivdep
#pragma omp simd
   for (...)
```

Cray clang rejects this with
`error: expected a for, while, or do-while loop to follow ivdep`.
**Fix: delete the `#pragma ivdep` line** at those sites — `omp simd` already
asserts independence. Nothing else in the tree fails to compile.

## Annotation: SW4 uses the `.C` extension, which breaks the clang annotator

This is the single biggest trap. SW4's C++ files are `*.C` (uppercase).

- `clang_annotate_project` *discovers* them (its `C_EXTS` test lower-cases the
  suffix), but
- `source_parser._try_clang` decides the clang language with
  `lang = "c" if path.suffix.lower() == ".c" else "c++"` — so a `.C` file is
  parsed as **C**, which fails instantly on any C++ construct
  (`-std=c++11` is itself rejected with `-x c`).

**Workaround that works today:** rename `src/*.C` → `src/*.cpp` in the annotated
tree and update the 4 `.C` references in the Makefile
(`src/rhs4sgcurv.C`, `src/version.C`, the `%.o:src/%.C` pattern rule, and
`src/RandomizedMaterial.C`). The renamed tree builds identically. Verify with a
pristine build of the renamed tree *before* annotating.

**The real fix** (not yet applied) is in the MCP tools: treat `.C` as C++ in
`source_parser._try_clang` and in `clang_syntax_check`'s `language="auto"`
branch. Until then always pass `language="cpp"` explicitly.

Useful `compile_flags` for the annotator:
`-std=c++11 -I<annotated>/src -I<annotated>/src/double -I<mpi include dir>`.

### The brace pass self-disables (and that is FINE for SW4)

`add_braces_c` does not receive `compile_flags`, so it cannot find `mpi.h` and
skips itself with a warning on every file that includes MPI. **This is harmless
here**: in C++ mode the annotator emits RAII `DFTRACER_CPP_FUNCTION()` only, at
the first line of each function body, and **no `FUNCTION_END` macros at all** —
so there is no END-into-a-braceless-if corruption path.

Where the brace pass *did* run (files that parse without MPI headers) it
corrupted 2 files, `fastmarching.C` and `sacsubc.C`, by inserting a `{` in the
middle of a multi-line `if (a || b || c) return x;` condition — the known
`bug-clang-add-braces-multiline-call-corruption` shape. Only `fastmarching`
failed to compile; `sacsubc` compiled but was still modified.

**Deterministic repair:** strip every line containing `DFTRACER_CPP_` plus the
inserted `#include <dftracer/dftracer.h>` from the annotated file and diff the
remainder against the pristine source. Any leftover extra lines are inserted
braces; delete them by index. This restores byte-identical pristine content with
the annotations intact, and needs no re-annotation.

### Annotator bug: the dftracer include lands after the LAST `#include` in the file

The annotator inserts `#include <dftracer/dftracer.h>` after the file's last
`#include` line. SW4 has files with `#include` directives near the **end** —
`EW.C` includes `AllDims.h` at line 8601 of 8683 — so the include landed at line
8602 while the first macro use was at line 543. Result: ~200
`use of undeclared identifier 'DFTRACER_CPP_FUNCTION'` errors.

Affected in SW4: `EW.C` and `RandomizedMaterial.C` (2 of 121).

**Check after every annotation run:** the include's line number must be lower
than the first `DFTRACER_CPP_` line. Move it to just after the last
**conditional-nesting-depth-0** `#include` that precedes first macro use.

### Annotator bug: `DFTRACER_CPP_INIT` placed after early exits in `main`

In `main.C` the annotator anchored `INIT` after `MPI_Comm_size` (line ~150) but
put `REGION_END` + `FINI` on the three argument-error exits **above** it
(lines ~110-135). Those paths would call `FINI` on an uninitialized logger.

**Fix:** move the `INIT` / `REGION_START` / `REGION_DYN_UPDATE` block to
immediately after `MPI_Comm_rank(...)`, which is above every exit.
`main` has **4 exits**, each already `MPI_Finalize(); return ...;` — SW4 calls
`MPI_Finalize` directly (no wrapper), so the anchor logic and lint rule L3 both
work here.

## EXACTLY ONE function must be excluded: `EW::getDepth`

`bool EW::getDepth(x, y, z, depth)` (`EW.C`) is called **once per grid point**
from the material-setup loops. On a trivial 254k-point test grid it alone
produced **554,496 of 568,504 app events (97%)** and inflated wall time from
1.37 s to 3.86 s (2.8x). At production grid sizes it is unusable.

```
clang_annotate_file(..., exclude_functions='["getDepth"]')
```

After excluding it: 1.59 s wall (16% over pristine) and a well-balanced trace.

**Nothing else needs excluding.** The next-busiest functions are the source
time-function evaluations (`getFxyz`, `Gaussian_tt`, `getFxyztt`) at ~150k
events over 715 timesteps — these scale with timesteps, not grid points, and are
genuinely interesting. The stencil kernels (`rhs4th3fortsgstr_ci`, `bcfortsg_ci`,
`predfort_ci`, `dpdmtfort_ci`) each fire ~1-2x per timestep per rank, which is
exactly the right granularity.

Do **not** annotate `Sarray.h` / `Farray.h` — they are per-element inline
accessors and would be far worse than `getDepth`.

## Coverage expectations

121 C++ files → **112 annotated, 1059 instrumented spans, 1059 `comp=` UPDATEs**.
The 9 unannotated files are legitimate:
`ESSI3DHDF5`, `readhdf5`, `sachdf5` (entire bodies inside `#ifdef USE_HDF5`,
which is off), and `ForcingTwilight`, `MaterialData`, `MaterialProperty`,
`Patch`, `Polynomial`, `SecondOrderSection` (all functions below the cost
threshold).

**Liveness is low by design and that is correct:** a single Cartesian LOH.1 run
fires ~143 distinct annotated functions. The rest are alternate source time
functions (Brune, C6SmoothBump, Dirac, Liu, ...), curvilinear-grid and
mesh-refinement code, anisotropic materials, the pfile/rfile/sfile/ifile/GMG
material readers, checkpoint/restart, ESSI/HDF5 output, the twilight/energy test
harnesses, and the `sw4mopt` inversion code — none of which a Cartesian
point-source run touches.

`moptmain.C` also contains a `main()` and gets INIT/FINI; it links only into
`sw4mopt`, not `sw4`, so this is harmless.

## Run sizing (validated, 2 nodes on an MI300A/Cray system)

SW4's `examples/scec/LOH.1-h50.in` is the canonical benchmark but too large for a
smoke test. Coarsen the grid and shorten the time:

| grid `h` | domain | grid points | `time t` | steps | 2-node wall |
| --- | --- | --- | --- | --- | --- |
| 400 | 30k x 30k x 17k | 0.25 M | 1.0 | 15 | ~1.5 s (1 rank) |
| 100 | 30k x 30k x 17k | 15.5 M | 3.0 | 179 | **16 s** |
| 75  | 30k x 30k x 17k | 36.7 M | 9.0 | 715 | **89-112 s** |

Launch geometry that worked: `-N2 -n16 -c11`, `OMP_NUM_THREADS=11`
(8 ranks/node x 11 threads = 88 of 96 cores, leaving room for the
`dftracer_service` daemon's pinned core).

`fileio path=<dir>` sets SW4's output directory — point it at the parallel
filesystem. `rec ... usgsformat=1` and `image mode=... timeInterval=...` produce
genuine POSIX write traffic (117 files / 65 MB for the h=75 case).

## What the trace actually contains (h=75, 2 nodes, 16 ranks)

App traces (16 files, 1,097,296 events): `CPP_APP` 916,610 · `p2p` 94,868
(`MPI_Sendrecv` dominates — SW4 halo-exchanges with Sendrecv) · `STDIO` 37,218 ·
`env` 28,186 (`MPI_Wtime`) · `POSIX` 6,690 · `comm` 3,596 · `collective` 2,208
(`MPI_Barrier`, `MPI_Allreduce`, `MPI_Gather`) · `papi` 1,408 · `datatype` 720 ·
`topology` 64.

**SW4 does real file I/O — POSIX events are expected.** Missing POSIX would be a
defect, not a property of the app.

## Correctness validation protocol (passed)

pristine binary, annotated binary with `DFTRACER_ENABLE=0`, and the fully-traced
annotated binary all produced **byte-identical** stdout (modulo the compile
timestamp banner) and byte-identical receiver output files. Run this before
trusting any SW4 annotation.

See also [[system-tuolumne]], [[software-papi]],
[[bug-clang-add-braces-multiline-call-corruption]],
[[bug-clang-annotator-silent-zero-functions]],
[[feedback-dftracer-service-node-counters]].
