---
name: software-gcc
description: GCC/GNU toolchain knowledge that is system-agnostic - how to find which GCC versions a site offers, pair GCC with an MPI wrapper, switch compilers on an existing build tree, and borrow GCC's C++ headers/libstdc++ for clang. Each fact points back to the system-* skill that holds the site-specific module names. Load for any build that needs GCC/GNU instead of the site default compiler.
metadata:
  type: software
---

## Scope

This skill is deliberately site-neutral. It records what is true of GCC on any
HPC system and tells you where the site-specific module names live. Never copy
a module name from one system to another - re-derive it with the probes below
and read the matching `system-*` skill.

## Probing what a site offers (any system)

```bash
module -t avail gcc            # Lmod: every gcc-related module, grouped by modulefile dir
which gcc g++ gfortran         # what is on PATH right now (may NOT be what an MPI wrapper uses)
gcc --version | head -1
module -t avail <mpi-module>   # then list the MPI install's per-compiler wrapper dirs
```

Findings to expect and distinguish:

- There can be MORE THAN ONE family of GCC modules (a vendor/PE family and a
  site toolchain family). They differ in version set and in how MPI is paired.
- The `gcc` on `PATH` in a bare shell is often the newest site toolchain GCC,
  which may have no matching MPI wrapper.
- A GCC version being installed does not mean an MPI build exists for it - see
  the next section.

## Pairing GCC with MPI

- Cray-style MPI installs one wrapper directory PER compiler family and
  version (`.../<mpi-version>/<fabric>/<family>/<compiler-version>/bin/mpicc`).
  Only the versions actually present there can be used; list the directory
  before choosing a GCC module (`ls .../<fabric>/gnu/`).
- Site-MPI modules (e.g. MVAPICH2 on LC commodity clusters) instead resolve to
  a build matching the currently loaded GCC automatically when the compiler
  module is loaded first.
- Order matters: load/swap the compiler, THEN the MPI module, THEN
  `export CC=$(which mpicc) CXX=$(which mpicxx)` (see
  [[feedback-cc-cxx-mpi-selection]] in memory pointers).

## Switching compilers on an existing build tree

- CMake pins the compiler in `CMakeCache.txt` and refuses to swap it silently:
  `rm -rf` the build directory first.
- Autotools: re-run `configure` with the new `CC`, and `make clean`.
- A Python venv with compiled wheels built under one compiler should be rebuilt
  rather than mixed.

## GCC as a header/libstdc++ provider for clang

Clang-based compilers (Cray clang, hipcc, amdclang) locate libstdc++ headers
from an installed GCC toolchain. `mpicxx -v` prints `Selected GCC installation:`
- check it when a clang build fails on missing/old C++ standard-library
  headers, and choose the GCC toolset deliberately rather than inheriting
  whichever was found first. Details and the site's toolset path:
  [[system-tuolumne]].

## Where the site-specific facts live

| System | GCC/GNU details | Read |
|---|---|---|
| Tuolumne (AMD MI300A, Cray PE, Flux) | Two GCC module families (Cray-PE `gcc-native` 10.3-13.2 and site toolchain `gcc` 10.3.1-13.3.1); bare-shell `gcc` is 13.3.1; cray-mpich ships a GNU wrapper dir for `gnu/11.2` ONLY (checked under both 9.0.1 and 9.1.0), so GNU+MPI means `PrgEnv-gnu` + `gcc-native/11.2` | [[system-tuolumne]] |
| Dane (CPU-only Sapphire Rapids, Slurm) | Site `gcc` 10.3.1-13.3.1; pairing used for dftracer: a GCC 12.1.1 toolchain module plus an MVAPICH2 module that auto-selects the matching build | [[system-dane]] |
| Matrix (H100 + Sapphire Rapids, Slurm) | Same GCC set as Dane; the CUDA version in use caps the usable GCC (CUDA 12.6 supports GCC <= 13), so pick GCC for CUDA first, MPI second | [[system-matrix]] |
| Tioga (AMD GPU, Cray PE) | Default environment is Cray compiler, not GCC; no GNU pairing recorded yet | [[system-tioga]] |
| Containers | Compiler comes from the image, not modules | [[system-container]] |

If the system is not in this table, run the probes above, then register the
system (`system_save`) and add its row here with a link to its `system-*`
skill. Do not invent a pairing: a missing row means "not yet recorded".

## Related skills

[[software-mpi]], [[software-cmake]], [[software-autotools]],
[[software-rocm]], [[system-detect]]
