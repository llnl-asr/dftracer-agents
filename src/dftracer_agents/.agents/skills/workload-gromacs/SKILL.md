# Skill: workload-gromacs

GROMACS (GROningen MAchine for Chemical Simulations) — molecular dynamics simulation package.
Build system: CMake. Languages: C++, CUDA/HIP, Fortran, Python bindings.

Cross-references: [[software-cmake]] [[software-hdf5]] [[system-tuolumne]]

---

## Build System

- **CMake 3.28+ required** (system default 3.24.2 is too old)
- Use `module load cmake/3.29.2` on Tuolumne
- HDF5 optional but recommended for checkpoint I/O
- MPI support via `-DGMX_MPI=ON`
- GPU support: `-DGMX_GPU=HIP` for AMD MI300A (ROCm)

## CMake Configuration

### Required flags

```bash
cmake -S source -B build \
  -DCMAKE_INSTALL_PREFIX=<install_dir> \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DGMX_MPI=ON \
  -DGMX_GPU=HIP \
  -DGMX_OPENMP=ON \
  -DGMX_DOUBLE=OFF \
  -DBUILD_TESTING=OFF \
  -DGMX_BUILD_HELP=OFF \
  -DGMX_BUILD_MANUAL=OFF \
  -DGMX_INSTALL_LEGACY_API=ON \
  -DHDF5_DIR=<session_hdf5_prefix> \
  -DMPI_C_COMPILER=/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicc \
  -DMPI_CXX_COMPILER=/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicxx
```

### Critical: disable documentation build

**Problem:** GROMACS docs require `sphinx_copybutton` Python module not available in the
system anaconda environment. Documentation build fails even when `-DGMX_BUILD_HELP=OFF`.

**Fix:** Always add `-DGMX_BUILD_MANUAL=OFF` to skip PDF manual generation entirely.

Without this flag, the build succeeds for all binaries but fails at the very end during
`make install` when trying to build the PDF manual with Sphinx, blocking the install step.

Symptom:
```
sphinx.errors.ExtensionError: Could not import extension sphinx_copybutton 
(exception: No module named 'sphinx_copybutton')
make[2]: *** [docs/manual/CMakeFiles/gromacs_pdf.dir/build.make:71: docs/manual/gromacs.tex] Error 2
```

Observed: 2026-08-07, Tuolumne, GROMACS main branch (latest)

---

## HDF5 Integration

GROMACS can optionally use HDF5 for checkpoint file format. System HDF5 1.10.5 is detected
but rejected by GROMACS (requires 1.10.7+). Build HDF5 1.14.x from source per [[software-hdf5]]:

```bash
# In session workspace:
curl -fkL https://support.hdfgroup.org/ftp/HDF5/releases/hdf5-1.14/hdf5-1.14.3/src/hdf5-1.14.3.tar.gz \
  -o hdf5-1.14.3.tar.gz
tar xf hdf5-1.14.3.tar.gz && cd hdf5-1.14.3
CC=mpicc ./configure --prefix=$PWD/../hdf5_1.14 --enable-parallel --enable-shared \
  --enable-build-mode=production --with-zlib=/usr
make -j8 && make install
cd .. && sed -i 's/H5Aread_async(chid_t attr_id/H5Aread_async(hid_t attr_id/' \
  hdf5_1.14/include/H5Apublic.h
```

Then pass `-DHDF5_DIR=$PWD/hdf5_1.14` to GROMACS cmake.

---

## Smoke Test Command

TBD - typical GROMACS smoke test uses `gmx mdrun` on a small test system.
Example to be added after first successful build.

---

## Lessons

### 2026-08-07: Documentation build blocks install even when help disabled

Date: 2026-08-07
System: Tuolumne (Cray PE, Python 3.13.2 anaconda)
Context: Building GROMACS main branch with `-DGMX_BUILD_HELP=OFF`

Error:
```
sphinx.errors.ExtensionError: Could not import extension sphinx_copybutton
```

Root cause: `-DGMX_BUILD_HELP=OFF` only disables HTML help, not the PDF manual.
The PDF manual target is built unconditionally during `make install`, and Sphinx
configuration requires `sphinx_copybutton` which is not in the system Python.

Fix: Add `-DGMX_BUILD_MANUAL=OFF` to CMake flags.

Do not: Install sphinx_copybutton into system Python (no sudo); do not modify
GROMACS's docs/manual/CMakeLists.txt (would diverge from upstream).

---

## Failed Configurations

<!-- New failed-config entries appended below during optimization loops -->

None recorded yet.
