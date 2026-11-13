---
name: feedback-never-prebuilt-when-config-knobs-needed
description: dftracer with MPI/ROCm/HDF5 tracing comes from the prerelease plus dftracer-compose (session_install_dftracer's default, prebuilt="compose"), not the plain prerelease wheel; verify what was linked
metadata:
  type: feedback
---

When the traced workload needs dftracer-side features -- MPI-IO interception, HDF5 tracing, HIP (ROCm) tracing, PAPI --
install dftracer with `session_install_dftracer` in its default mode, `prebuilt="compose"`: the dftracer prerelease with
`dftracer-modules` (its optional modules, prebuilt for many MPI, HDF5, PAPI, hwloc, ROCm and CUDA versions), then
`dftracer-compose` run in the session's `env.sh` links the modules this run needs against the app's own modules, and
builds from the shipped sources any variant that is not prebuilt. Never the plain prerelease wheel
(`prebuilt="prerelease"`), which has none of them.

**Why:** the original rule (2026, YGM pipeline) was "never prebuilt when MPI/ROCm/HDF5 must be configured -- always build
from source": the prerelease wheel then was one fixed build without MPI, and `ldd libdftracer_core.so | grep -i mpi`
showed nothing linked. The user corrected that ("never use prebuild when u have options to configure like adding MPI
ROCM or HDF5"). Since 2026-11 dftracer's prerelease ships its modules separately and composes them per machine, so the
configuration happens at install time without a source build; the user then asked that every install use the
prerelease and compose what the current run needs.

**How to apply:** let detection (or the mpi/hdf5/hip/papi arguments) say what the run needs and keep the default mode.
Read the result's `compose`: `linked` is what dftracer has, `absent` is a library this machine does not have (load its
module in `env.sh` and re-run `scripts/compose_dftracer.sh`), and a module that did not build fails the tool. A
green install still is not proof: check `dftracer_install_info --composition` or the composition in the result. Use a
source build (`prebuilt="auto"`) only when the user asks for one or for a dftracer branch that is not a release. See
[[tools-dftracer]] and [[workload-ygm]].
