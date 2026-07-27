---
name: feedback-mpi4py-cray-soname-mismatch
description: manylinux mpi4py wheel dlopens libmpi.so.12 but cray-mpich provides libmpi_cray.so.12 — needs a symlink shim
metadata:
  type: feedback
---

`from mpi4py import MPI` fails with `libmpi.so.12: cannot open shared object file` on Cray systems even with cray-mpich loaded and MPI4PY_MPIABI=mpich set.

**Why:** The manylinux mpi4py wheel's `MPI.mpich.*.so` binding dlopens the SONAME `libmpi.so.12`, but cray-mpich ships the equivalent library as `libmpi_cray.so.12` — a different filename with the same ABI.

**How to apply:** Create a local symlink shim directory on LD_LIBRARY_PATH containing `libmpi.so.12 -> libmpi_cray.so.12.0.0` (resolve the real cray-mpich lib path first), and keep `MPI4PY_MPIABI=mpich` exported. See [[feedback_mpi4py_install]], [[feedback-cray-ld-library-path-fortran-runtime]].
