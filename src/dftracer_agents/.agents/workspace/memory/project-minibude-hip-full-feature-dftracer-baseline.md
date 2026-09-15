---
name: project-minibude-hip-full-feature-dftracer-baseline
description: miniBUDE (UoB-HPC docking mini-app) HIP backend annotated + traced on Tuolumne at 2 nodes with the maximum dftracer feature set — GPU + PAPI + variorum + node counters all verified PRESENT from the trace; two annotation traps found and fixed
metadata:
  type: project
---

Full-feature dftracer baseline for **miniBUDE** (UoB-HPC/miniBUDE, molecular
docking, C++/CMake) on Tuolumne (MI300A). Complete: annotated, built, run on
2 nodes, event inventory proven from the trace. All app-specific knowledge is
in the new **`workload-minibude`** skill — load that, don't re-derive.

**Backend chosen: `-DMODEL=hip`** (`--offload-arch=gfx942`). It is the backend
that produces GPU events on an AMD system; it built and validated first try
(`valid: true`, `max_diff_%: 0.008`, ~3780 GFLOP/s).

**miniBUDE is NOT MPI.** No `mpi.h`, no `MPI_` symbol in `src/`. A 2-node run is
2 independent single-process instances. MPI trace categories are legitimately
absent — that is expected, not a defect.

**dftracer must come from czgitlab, not GitHub, for PAPI + variorum.**
GitHub `develop` (v2.0.3) built green with `features=['hip','papi','variorum','hwloc']`
reported by the install tool, but the installed `dftracer_config.hpp` had
**neither** `DFTRACER_PAPI_TRACING_ENABLE` **nor** `DFTRACER_VARIORUM_ENABLE`, and
`rocprofiler_configure` was **not** exported (so GPU tracing would have lost the
registration race). Re-installing from
`ssh://git@czgitlab.llnl.gov:7999/dftracer/dftracer.git@develop` gave all three.
**The install tool's `features_enabled` list reflects what was REQUESTED, not what
was COMPILED — always grep `dftracer_config.hpp` and `nm -D | grep -w
rocprofiler_configure`.** See [[feedback-never-prebuilt-when-config-knobs-needed]].

**Two teardown bugs from earlier sessions did NOT reproduce on czgitlab develop:**
both ranks exited rc=0 with complete traces ending in the `end` event — no PAPI
sampler SIGSEGV ([[bug-dftracer-rocprofiler-configure-race-and-papi-sampler-segv]])
and no variorum heap corruption ([[bug-dftracer-variorum-linked-into-app-heap-corruption]];
`ldd` on the app shows no `libvariorum`, one `librocm_smi64` only, while
`dftracer_service` does link variorum). Those fixes have landed.

**Two annotation traps, both silent, both now in `workload-minibude`:**
1. `main` ends `return run<...>(...) ? ...`. The annotator's *correct*
   REGION_END -> FINI -> return ordering puts the entire computation after
   `DFTRACER_CPP_FINI()`. Generalises to any `main` whose last statement is
   `return <does-all-the-work>(...)`.
2. Everything interesting lives in `src/<model>/fasten.hpp`, a header the
   project annotator does not discover. Single-TU app, so hand-annotating the
   host-side methods is safe.

**Event inventory (2 nodes, `-i 5000`, 80 s wall, 122 362 app + 39 010 service events):**
PRESENT — `CPP_APP` 28, `KERNEL_DISPATCH` 10 003, `HIP_RUNTIME_API` 20 038,
`MEMORY_COPY` 12, `SCRATCH_MEMORY` 2, `papi` 1 337 (all 4 requested counters,
`multiplex: 0` on every sample = exact), `POSIX` 2 712, `STDIO` 77 130,
service `sys`/`io`/`net` + variorum `type 13` `gpu`/`power` 166.
ABSENT and expected — MPI, HDF5, `PAGE_MIGRATION` (explicit hipMalloc, no
managed memory).

**Tool defects found (reported, not patched — need an MCP server restart):**
* `session_configure`/`session_build_annotated` hardcode `CMAKE_BUILD_TYPE=RelWithDebInfo`;
  miniBUDE hard-errors on anything but Release/Debug.
* `extra_cmake_flags` cannot express a multi-token `-D` value: a space splits it
  into separate cmake args, a `;` truncates the command at the shell.
* `session_build_annotated` needs `build_subdir="source"` for the standard
  `annotated/source/` layout; its error message does not hint at that.
* `clang_syntax_check` hardcodes `g++ -std=c++14` with no `-D`/std pass-through,
  so it reports pure false positives on any C++17 project.
* `dftracer_stats --report categories` still returns `Events Scanned: 0`
  ([[bug-dftracer-stats-categories-zero-events]] reconfirmed).
* `dftracer_view` `!=` queries are pruned to zero by the bloom-filter chunk
  skip (`scanned=0 skipped=N, matched=0`) with no error — only `==` queries
  work for enumeration. The MCP `view` also grabs a FOREIGN flux allocation
  unless `allocation_id` is passed.

**Why:** asked for a maximal-feature dftracer baseline on miniBUDE and a
trace-proven inventory of which event types actually land.

**How to apply:** load `workload-minibude` before any miniBUDE work. On any
single-TU / header-only mini-app, check the statement right after
`DFTRACER_CPP_FINI()` and hand-annotate the model header before trusting a
green `clang_annotate_project` report. See [[system-tuolumne]],
[[bug-dftracer-service-hosts-must-be-pinned]],
[[bug-dftracer-service-start-blocks-flux-run]].
