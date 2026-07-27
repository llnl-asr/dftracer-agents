---
name: feedback-cray-ld-library-path-fortran-runtime
description: CRAY_LD_LIBRARY_PATH (set by cce/cray-mpich/cray-libsci modules) must be prepended before importing Cray-MPI-linked Python extensions
metadata:
  type: feedback
---

Importing any Cray-MPI-linked Python C-extension (dftracer, mpi4py) on Tuolumne can fail with `libmodules.so.1: cannot open shared object file` even after loading cce/cray-mpich/cray-libsci modules and setting LD_LIBRARY_PATH to the versioned CCE lib dir.

**Why:** The actual Fortran runtime libs (libmodules, libfi, libcraymath, libf, libu, libcsup) live under a path only exposed via the module-set `CRAY_LD_LIBRARY_PATH` variable, not the plain `/opt/cray/pe/cce/<ver>/cce/x86_64/lib` path.

**How to apply:** After loading cce/cray-mpich/cray-libsci modules, always do `export LD_LIBRARY_PATH="$CRAY_LD_LIBRARY_PATH:$LD_LIBRARY_PATH"` before activating any venv or importing dftracer/mpi4py. See [[feedback-cc-cxx-mpi-selection]], [[feedback-mpi4py-install]].
