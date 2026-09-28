---
name: feedback-never-prebuilt-when-config-knobs-needed
description: Never use dftracer's prebuilt/prerelease distribution when MPI/ROCm/HDF5 need to be configured — always build from source (czgitlab, LC-only)
metadata:
  type: feedback
---

Never install dftracer from a prebuilt/prerelease distribution (e.g. the Tuolumne `dftracer-dist` modulefile + `pip install --pre dftracer`) when the traced workload needs ANY dftracer-side build-time feature flag: MPI-IO interception, ROCm/HIP tracing, or HDF5 tracing. Always build from source instead, with the matching `DFTRACER_ENABLE_MPI`/`DFTRACER_ENABLE_HDF5`/`MPICC`/`MPICXX`/`HDF5_ROOT` env vars set before the pip/cmake build.

**Why:** corrected mid-session on a YGM (MPI messaging library) pipeline — the prebuilt prerelease wheel was used first (faster, no compile step) and worked fine for FUNCTION-mode app-level annotation tracing, but `ldd libdftracer_core.so | grep -i mpi` showed it had NO MPI library linked at all. The prebuilt wheel is one fixed build with whatever flags its own CI used; it does not expose a way to opt into MPI/ROCm/HDF5 support after the fact. The user explicitly corrected this: "never use prebuild when u have options to configure like adding MPI ROCM or HDF5. Always need to build from source."

**How to apply:** before choosing a dftracer install path, ask whether the workload needs dftracer's own MPI-IO interception, HDF5 tracing, or HIP tracing (not just app-level FUNCTION-mode annotation around MPI/HDF5/HIP calls, which works with any build). If yes, build from source — LLNL's internal GitLab is the canonical source for these projects, same org, one repo per package:
```
https://github.com/llnl-asr/dftracer.git
https://github.com/llnl-asr/dftracer-utils.git
https://github.com/llnl-asr/pydftracer.git
```
(dfanalyzer, dfdiagnoser under the same org.) **`czgitlab.llnl.gov` is only reachable from inside the LC (Livermore Computing) network** — confirmed working via SSH from Tuolumne (an LC system) without extra key setup, but this source will NOT be reachable from outside LC (a non-LC or external/sandboxed environment) — fall back to PyPI or the public GitHub mirror (`github.com/LLNL/dftracer`) there. Install order: dftracer BEFORE dftracer-utils (stale header collision otherwise, see [[tools-dftracer]] RULE 3). Only use the prebuilt distribution when the workload genuinely needs nothing beyond plain FUNCTION-mode tracing with zero dftracer-side feature flags — and even then, ALWAYS verify with `ldd` rather than assuming a feature is present, since a green install/import does not confirm what was actually compiled in. See [[tools-dftracer]] and [[workload-ygm]] for the full install-source comparison and the YGM session this was learned on.
