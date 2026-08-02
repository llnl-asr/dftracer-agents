---
name: feedback_dftracer_install_rocm_mpi
description: "dftracer install on tuolumne — skip ROCProfiler unless app uses ROCm; MPI is compatible, just pass MPI impl version + headers"
metadata: 
  node_type: memory
  type: feedback
---

**Canonical home:** see the `dftracer-install` skill and `software-rocm` (ROCm-only-
when-app-uses-it rule, generalized).

When installing dftracer on tuolumne (or corona):

- **ROCProfiler is only needed if the traced app actually uses ROCm/GPU.** Flash-X Sedov 3D (and other CPU-only workloads) do NOT use ROCm, so do NOT enable or require ROCProfiler in the dftracer build. Enabling it forces a ROCm dependency that is unnecessary and can break the install.
- **MPI is compatible on tuolumne** — do not apply elaborate MPI workarounds. Simply pass the MPI implementation version and headers (the Cray MPICH 9.0.1 / GNU 11.2 include path) to the dftracer build.

**Why:** The user corrected a dftracer-build-dftracer dispatch that was over-engineering ROCProfiler and MPI handling. dftracer only needs ROCProfiler for GPU tracing, and tuolumne's MPI just needs the right headers/version.

**How to apply:** In dftracer install (session_install_dftracer), disable ROCProfiler for CPU-only apps like Flash-X, and pass MPI version + include dir rather than special-casing MPI. Relates to [[feedback_dftracer_install_env_vars]] and [[feedback_tuolumne_modules]].
