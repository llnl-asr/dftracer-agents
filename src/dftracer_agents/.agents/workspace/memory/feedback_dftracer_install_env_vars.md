---
name: feedback_dftracer_install_env_vars
description: dftracer setup.py reads ENV VARS not CMAKE_ARGS/config-settings; Cray HDF5 needs patched headers + module unload
metadata: 
  node_type: memory
  type: feedback
---

**Canonical home:** see the `dftracer-install` skill and `tools-dftracer` (CC/CXX/
env-vars-before-pip-install section) for the generic version of this rule.

Installing dftracer (llnl/dftracer @develop) with MPI/HDF5 on Tuolumne.

**Why:** dftracer's `setup.py` reads build options from **environment variables**
(`DFTRACER_ENABLE_MPI=ON`, `DFTRACER_ENABLE_HDF5=ON`, `DFTRACER_ENABLE_HIP_TRACING=OFF`,
`DFTRACER_ENABLE_FTRACING=ON`, `HDF5_ROOT`, `MPI_C_COMPILER`, `MPI_CXX_COMPILER`).
It does NOT honor `CMAKE_ARGS`, pip `--config-settings=cmake.args`, or scikit-build
settings — those are silently ignored and it builds all-OFF defaults.

**How to apply:**
1. `export DFTRACER_ENABLE_*` env vars before `pip install git+...@develop`.
2. Turn HIP tracing OFF on Tuolumne (no rocprofiler-sdk headers → fatal build error).
3. Cray HDF5 header has a `chid_t` typo in H5Apublic.h:932 that breaks dftracer's
   C++/brahma frontend. Fix: copy the module's `include/` to a writable dir, `sed`
   `H5Aread_async(chid_t` → `hid_t`, symlink its `lib`, set HDF5_ROOT to that dir,
   AND `module unload cray-hdf5-parallel` during the build (else the Cray `cc`
   wrapper re-injects the unpatched `-I.../cray/20.0/include`).
4. Verify: grep DFTRACER_MPI_ENABLE/HDF5_ENABLE/FTRACING_ENABLE ==1 in
   `.../dftracer/include/dftracer/core/dftracer_config.hpp`.

Related: [[feedback_tuolumne_modules]] [[feedback_hdf5_dftracer_stack]] [[project_package_restructure]]
