---
name: feedback-mpi4py-install
description: mpi4py install recipe for Python 3.13 + cray-mpich on Tuolumne — manylinux wheel + manual extract + patchelf + MPI4PY_MPIABI=mpich
metadata: 
  node_type: memory
  type: feedback
---

**Canonical home:** see the `software-mpi` skill and `system-tuolumne` for the
mpi4py install recipe.

On Tuolumne with Python 3.13 + cray-mpich/9.1.0, mpi4py requires a specific install recipe:

1. **Download** the manylinux wheel (don't build from source): `pip download 'mpi4py==4.1.1' --no-deps -d $SESSION/tmp`
2. **Extract manually** with Python — `pip install` fails on NFS site-packages with `[Errno 2]` on the output `.so` (atomic rename across filesystem boundary)
3. **patchelf** the MPICH backend: `patchelf --replace-needed libmpi.so.12 libmpi_cray.so MPI.mpich.cpython-313-x86_64-linux-gnu.so`
4. **Set at runtime**: `export MPI4PY_MPIABI=mpich` — ABI auto-detection fails on tuolumne[1764+] nodes

**Why:** mpi4py 4.x ships separate `MPI.mpich.*` and `MPI.openmpi.*` backends. On newer Tuolumne nodes the auto-detect picks neither. `libmpi.so.12` (manylinux default) doesn't match `libmpi_cray.so` (cray-mpich's name). And pip's internal rename-to-NFS fails silently.

**How to apply:** Any time mpi4py needs to be installed/reinstalled for Python 3.13 on Tuolumne, use this recipe. See `annotated/ScaFFold/scripts/install.sh` for the canonical implementation. Never use `--no-binary=mpi4py` on Python 3.13 + NFS.

Related: [[feedback-tuolumne-modules]]
