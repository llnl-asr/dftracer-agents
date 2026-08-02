---
name: feedback-hdf5-dftracer-stack
description: h5py + dftracer library stack consistency rules on Tuolumne — RPATH patchelf required after every install; NEVER set DFTRACER_DISABLE_IO
metadata: 
  node_type: memory
  type: feedback
---

**Canonical home:** see the `software-hdf5` skill (RPATH/patchelf consistency rules).

# h5py + dftracer HDF5 Library Stack

Keep the library stack consistent across all C-extension packages. Validated on Tuolumne with ScaFFold HDF5 benchmarks.

**Why:** dftracer pip wheels link against system `libhdf5.so.103` (1.10.x); h5py source-builds against session HDF5 (`libhdf5.so.310` 1.14.5). Two HDF5 instances in the same process → GOTCHA hooks inherited by forked DataLoader workers crash with `RuntimeError: Not a property list class`.

**How to apply:**

1. After `pip install --no-binary=h5py h5py`, patchelf all h5py `.so` files to strip anaconda RPATH contamination:

```bash
for so in "$VENV/lib/python3.13/site-packages/h5py/"*.so; do
  patchelf --set-rpath "$SESSION/install/hdf5/lib" "$so"
done
```

2. Patchelf dftracer C libs to match session HDF5:

```bash
for so in "$VENV/lib/python3.13/site-packages/dftracer/lib64/libdftracer_core.so" \
          "$VENV/lib/python3.13/site-packages/dftracer/lib64/libdftracer_preload.so"; do
  patchelf --replace-needed libhdf5.so.103 libhdf5.so.310 "$so"
  patchelf --set-rpath "$SESSION/install/hdf5/lib" "$so"
done
```

3. Verify stack consistency after every install:

```bash
ldd "$VENV/lib/python3.13/site-packages/h5py/defs.cpython-313-x86_64-linux-gnu.so" | grep hdf5
```

Must show ONLY the session path — no `/usr/lib64/libhdf5` or anaconda paths. See [[feedback_tuolumne_modules]] for module environment.

**NEVER set `DFTRACER_DISABLE_IO=1`** — GOTCHA interception must stay active for complete HDF5 I/O tracing. The correct fix is always library stack consistency via patchelf.
