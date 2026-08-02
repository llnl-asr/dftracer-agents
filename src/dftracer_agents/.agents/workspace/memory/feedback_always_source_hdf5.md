---
name: feedback_always_source_hdf5
description: Always install HDF5 from source into the session workspace; never use Cray/system HDF5 module
metadata: 
  node_type: memory
  type: feedback
---

**Canonical home:** see the `software-hdf5` skill (already codifies this rule in full).

Always build HDF5 from source into the session workspace (`<ws>/install_hdf5`) and
point the app + dftracer at it. Never load or link the Cray `cray-hdf5` /
`cray-hdf5-parallel` module (or any site HDF5 module) as the HDF5 the app builds against.

**Why:** Cray HDF5 has header typos (`chid_t`, tentative-def) that break dftracer/brahma
C++ linkage, and its version/ABI drifts with the PE (not a dftracer-compatible exact
series), silently degrading L2 optimizations. A source build gives a stable, known-good
prefix for reliable dftracer HDF5 tracing (RPATH/patchelf target one location).

**How to apply:** Use the module system only for the compiler/MPI toolchain (mpicc/mpicxx),
then build HDF5 1.14.5 from source with it. If a session was set up against Cray HDF5,
rebuild from source before proceeding. Codified in the [[software-hdf5]] skill.

Related: [[feedback_hdf5_dftracer_stack]] [[feedback_tuolumne_modules]]
