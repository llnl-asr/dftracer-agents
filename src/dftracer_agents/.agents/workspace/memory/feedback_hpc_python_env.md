---
name: feedback-hpc-python-env
description: Canonical 6-step Python HPC environment setup — steps 1-3 identical for install and run; single pip install; ldd verification required
metadata: 
  node_type: memory
  type: feedback
---

**Canonical home:** see the `system-tuolumne` skill ("Canonical Python environment
setup" section — Step 4 there was simplified per `feedback-cc-cxx-mpi-selection`).

# Canonical Python HPC Environment Setup

Steps 1–3 are **identical for install and run**. This is the only way to guarantee a consistent library stack at both build and execution time.

**Why:** C-extension packages (.so files) embed RPATH at compile time from whatever was on `PATH`/`LD_LIBRARY_PATH` during pip build. If the run environment differs (different modules, different LD_LIBRARY_PATH order), the wrong shared library loads at runtime, causing obscure errors (GOTCHA fork crashes, undefined symbols, wrong SONAME).

## Steps (install + run share 1-3)

**Step 1 — Load app modules**
Source the app's own install script module block exactly — same modules, same order, every time.

```bash
source /usr/share/lmod/lmod/init/bash
module load cce/21.0.0 cray-mpich/9.1.0 rocm/7.1.1 rccl/fast-env-slows-mpi
```

**Step 2 — Load extra software (source-built HDF5, custom libs)**
Put session-local paths FIRST in LD_LIBRARY_PATH so they shadow system/anaconda versions.

```bash
export LD_LIBRARY_PATH="$SESSION/install/hdf5/lib:$LD_LIBRARY_PATH"
```

**Step 3 — Activate the shared Python venv**
One venv for app + dftracer + all dependencies — never separate.

```bash
source "$SESSION/install/bin/activate"
```

## Install-only steps (after step 3)

**Step 4 — Set CC/CXX to the correct compiler**

```bash
# If MPI is in the module stack (cray-mpich):
export CC=cc CXX=CC
# Otherwise:
export CC=gcc CXX=g++
```

**Step 5 — Single pip install for everything**
One pip invocation resolves the full dependency graph consistently.
Special cases: mpi4py (manual wheel extract + patchelf, see [[feedback_mpi4py_install]]), h5py (HDF5_DIR=<session_hdf5> + patchelf RPATH after install, see [[feedback_hdf5_dftracer_stack]]).

**Step 6 — Verify with ldd**
Every key .so must resolve to session-local or module-provided library, NOT system/anaconda:

```bash
ldd "$VENV/lib/python3.13/site-packages/h5py/defs.cpython-313-x86_64-linux-gnu.so" | grep hdf5
ldd "$VENV/lib/python3.13/site-packages/dftracer/lib64/libdftracer_core.so" | grep hdf5
ldd "$VENV/lib/python3.13/site-packages/mpi4py/MPI.mpich.cpython-313-x86_64-linux-gnu.so" | grep mpi
```

If any .so resolves to the wrong path → fix with `patchelf --set-rpath` or `--replace-needed` immediately before any run.

The `install.sh` in `<session>/annotated/scripts/` is the single source of truth for the full stack.
