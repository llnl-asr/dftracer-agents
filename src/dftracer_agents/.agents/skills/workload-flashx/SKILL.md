---
name: workload-flashx
description: Flash-X-specific knowledge for the dftracer pipeline — build system (Python setup + GNU Make), Sedov 3D test problem, dangling symlinks in shallow clones, HDF5/flash.par pitfalls. Load this skill whenever working with Flash-X.
---

# Flash-X workload notes

Flash-X (git@github.com:Flash-X/Flash-X.git) is a Fortran/C++/C multiphysics
AMR code built with its own Python `setup` script + GNU Make (NOT cmake/autotools).

## Build system
- Top-level `./setup <Problem> -auto -<dim>` (e.g. `./setup Sedov -auto -3d`)
  generates an `object/` build dir; then `make` in that dir.
- Machine config lives under `sites/`; `Makefile.h` picks compilers/HDF5.
- Point HDF5 at a **source-built** install in the session workspace, never the
  Cray/system HDF5 module. See [[software-hdf5]] and [[feedback_always_source_hdf5]].

## Sedov 3D test problem
- `source/Simulation/SimulationMain/Sedov/` — flash.par + `tests/*.par` variants.
- Prior config: FUNCTION + DATA_DIR=all tracing, ~768 ranks, wall ≥ 30 min.

## Instrumentation strategy (Fortran-heavy code)
Flash-X is ~2600 Fortran (.F90) files vs 126 C / 6 C++ / 70 Python — dftracer's
clang auto-annotator only covers C/C++/Python. Decided approach (2026-07-08):
FUNCTION mode always; auto-annotate the C/C++ IO layer first; ASSESS I/O-path
coverage; if low, MANUALLY annotate key Fortran IO routines (`source/IO/IOMain/**`)
with dftracer's Fortran API. Always run with PRELOAD + DATA_DIR=all so HDF5/POSIX/MPI
I/O is captured at the library level regardless of source annotation. See
[[dftracer-preload-run]].

## Pitfalls (dated lessons: symptom → root cause → fix)
- 2026-07-07: `session_create` crashed with `FileNotFoundError` on
  `source/physics/sourceTerms/Stir/StirMain/TurbGen.h` → Flash-X ships **dangling
  symlinks** (targets not present in a `--depth 1` clone) and `shutil.copytree`
  dereferenced them → fix: copy with `symlinks=True, ignore_dangling_symlinks=True`
  (done in `mcp_tools/tools/session/session_tools.py` + siblings). Restart the MCP
  server after the fix.
- flash.par 80-column pitfall: long absolute output paths get silently
  truncated → keep a short `ds` symlink to the Lustre output dir and reference
  that in flash.par instead of the full path.
- Traces must land in `<WS>/traces/` (workspace), NOT the Lustre output dir —
  set `DFTRACER_LOG_FILE` to the workspace explicitly. See
  [[feedback_optimization_pipeline_traces]].
- 2026-07-08 (build): Cray PE `ftn`/`craycc` FAILED to build Flash-X (Fortran
  flag incompatibilities) → use the **GNU 11.2 MPI wrappers** at
  `/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/{mpif90,mpicc,mpicxx}` instead. Set
  `MPI_PATH=/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2` and
  `HDF5_PATH=<WS>/hdf5_1.14` in `sites/Prototypes/Linux/Makefile.h`.
- 2026-07-08 (build): gfortran strict checking vs MPI Fortran module interfaces →
  add `-fallow-argument-mismatch` to FFLAGS or the build fails on MPI type
  mismatches. Working Sedov 3D build: `./setup Sedov -auto -3d` then `make -j` in
  `object/`; exe is `object/flashx`. LD_LIBRARY_PATH must include CCE libs +
  `/usr/lib64` at link time.

## Build Session 2026-07-08: Flash-X Sedov 3D Baseline

Successfully built Flash-X Sedov 3D baseline executable on Tuolumne.

**Steps:**
1. Built HDF5 1.14.3 from source (curl from HDF5 FTP mirror) into workspace
2. Patched HDF5 header (H5Apublic.h line 932: chid_t → hid_t)
3. Initialized PARAMESH submodule before setup
4. Ran setup with HDF5_PATH env var: `HDF5_PATH=<WS>/hdf5_1.14 ./setup Sedov -auto -3d`
5. Updated object/Makefile.h HDF5_PATH to workspace
6. Built with `make -j4` in object dir

**Result:**
- Executable: `<WS>/baseline/source/object/flashx` (6.6M, dynamically linked)
- Exit code: 0 (success)
- Requires LD_LIBRARY_PATH to include `<WS>/hdf5_1.14/lib` at runtime

**Critical lessons:**
- PARAMESH submodule MUST be initialized before setup or it fails
- HDF5_PATH env var is honored by setup script to override Makefile.h defaults
- GNU 11.2 MPI wrappers are used (auto-detected, working correctly)
- No code errors, build completed successfully on first attempt after setup

