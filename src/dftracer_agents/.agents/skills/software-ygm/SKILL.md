---
name: software-ygm
description: Build/annotation caveats for YGM (github.com/llnl/ygm) — LLNL's header-only C++ MPI messaging library — discovered on Tuolumne (Cray PE). YGM is annotated/instrumented by dftracer (software-* naming, not workload-* — see [[feedback-software-vs-workload-naming]]); ygm-bench is the benchmark workload that exercises it.
metadata:
  type: software
---

## What YGM is

`github.com/llnl/ygm` — a header-only C++ MPI messaging/communication
library (async, active-message style). CMake build system, C++20 required
(not C++17 — YGM's dependencies, especially Boost 1.87 and spdlog/fmt, use
C++20 features). Dependencies (Boost, Cereal, spdlog) are auto-fetched via
CMake `FetchContent` during configure — no manual dependency install needed.
Optional `YGM_BUILD_TESTS=ON` builds the CTest suite under `test/`.

`github.com/llnl/ygm-bench` is the companion benchmark suite that exercises
YGM as a library — treat it as the workload driven by this software, the same
way an MPI application is the workload driven by [[software-mpi]].

## Annotation: header-only + split .ipp implementation needs care

YGM's implementation is split: `include/ygm/comm.hpp` declares the `comm`
class, `include/ygm/detail/comm.ipp` (included at the bottom of comm.hpp)
defines its method bodies out-of-line. Same pattern for
`collective.hpp`/`comm_router.hpp`/`mpi.hpp`/`comm_environment.hpp`.

- **Include order matters** (CP9 in [[dftracer-annotation-lessons]]): the
  entry `.cpp`'s `#include <dftracer/dftracer.h>` must come BEFORE
  `#include <ygm/comm.hpp>`, or every DFTRACER_CPP_* macro used inside the
  transitively-included `.ipp`/detail headers is undefined at parse time.
- **Better fix, used in this session**: don't rely on include order at all —
  add `#include <dftracer/dftracer.h>` directly to each annotated header
  (`collective.hpp`, `comm.ipp`, `comm_router.hpp`, `comm_environment.hpp`,
  `mpi.hpp`), right after `#pragma once`. This makes each header
  self-contained regardless of what includes it in what order, and matters
  because YGM has MULTIPLE test `.cpp` entry points (`test_comm.cpp`,
  `test_comm_2.cpp`, `test_barrier.cpp`, ...) — fixing include order in only
  ONE entry point leaves every other TU broken.
- `clang_extract_functions`/`clang_annotate_file` return 0 functions (not an
  error) for `.ipp` files that define `Class::method(...)` for a class
  declared in a sibling `.hpp` — no `-I` param to resolve `<ygm/...>` include
  paths. Manual annotation fallback required (same Rule 0 / comp= criteria
  applied by hand, verified via a real compiler + `clang_lint_annotations`).
  See [[dftracer-annotation-lessons]] LESSONS_LOG.md 2026-08-04.

## Build: CMakeLists.txt does not link dftracer by default

Annotating source files with `#include <dftracer/dftracer.h>` does NOT wire
the build to find/link `libdftracer_core.so` — YGM's `CMakeLists.txt` has no
knowledge of dftracer. Add a manual `IMPORTED` target (the packaged
`dftracer-config.cmake` from the prerelease wheel is broken — see
[[tools-dftracer]] "Prebuilt prerelease wheel's CMake package is broken") and
link it into the `ygm` interface target, driven by a `-DDFTRACER_ROOT=...`
flag pointing at the dftracer install prefix.

## Build: Cray-clang 20 vs spdlog/fmt — use PrgEnv-gnu

YGM's FetchContent'd spdlog (bundled fmt) fails to compile under Cray-clang
20.1.6 with a `consteval`-not-constant-expression error. Switch to
`PrgEnv-gnu` + `gcc-native/11.2` + the matching cray-mpich GNU wrapper
(`/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/mpicc`/`mpicxx`) — GCC 11.2.1
compiles cleanly. Full detail in [[system-tuolumne]]. `rm -rf build_ann`
before switching compilers on an existing CMake build tree.

## Smoke test

`test/test_comm.cpp` is a good minimal single-process smoke-test entry
point — confirmed producing real `CPP_APP`-category trace events from the
annotated comm/collective/barrier functions (`post_new_irecv`,
`check_completed_sends`, `local_process_incoming`, `barrier_reduce_counts`,
`priv_barrier`, `comm_setup`, etc.) plus `POSIX` and `dftracer` init/metadata
events. 194 total events on a single-rank run — confirms FUNCTION-mode
tracing works end-to-end for this library without needing dftracer's own
MPI-IO interception (the prerelease wheel has none linked in — see
[[tools-dftracer]]).

## ygm-bench: the traced benchmark, not part of this skill

`ygm-bench` (github.com/llnl/ygm-bench) is the traced BENCHMARK/workload that
exercises YGM — same role IOR plays for [[software-hdf5]]/[[software-mpi]].
Building it against this session's annotated YGM tree, its own
`DFTRACER_CPP_INIT`/`FINI` requirement, the hot-loop over-annotation +
selective-aggregation fix, the `MPI_Iallreduce` rank-density collapse, irecv
buffer OOM tuning, and the confirmed >=10-minute scaled-run config all live in
**[[workload-ygm-bench]]** — load that skill for anything about running or
scaling ygm-bench. This skill (`software-ygm`) stays scoped to the YGM
library itself: its own annotation/build/smoke-test caveats.

## Build: from-source dftracer, and matching its MPI ABI to YGM's own toolchain

The prebuilt/prerelease dftracer wheel has NO MPI support compiled in
(`ldd libdftracer_core.so` shows zero MPI libs) — fine for FUNCTION-mode
C++ tracing alone but insufficient if MPI-IO interception is ever needed.
For a from-source build, pass `DFTRACER_ENABLE_MPI=ON`,
`DFTRACER_MPI_IMPL=CRAYMPICH`, `BRAHMA_MPI_VERSION=900001` via
`DFTRACER_CMAKE_ARGS`, and **build dftracer itself with the SAME compiler/MPI
wrapper as the annotated app** (see [[software-mpi]] and
[[system-tuolumne]]). Building dftracer with Cray-clang+MPI while the app is
built with `PrgEnv-gnu` links two different MPI runtimes
(`libmpi_cray.so.12` AND `libmpi_gnu_112.so.12`) into the same process — a
documented double-free-at-exit crash risk, not just an inefficiency. Verify
with `ldd libdftracer_core.so | grep mpi` before trusting a from-source
build. See [[tools-dftracer]] for the prebuilt-vs-source decision and the
czgitlab source URL.

## Session layout note

The repo is nested as `annotated/source/CMakeLists.txt` (matching the
top-level `source/` tree), so `session_build_annotated` needs
`build_subdir="source"` — this exposed and got a real fix for a
`session_build_annotated` wiring bug where `build_subdir` was silently
ignored for cmake/autotools/meson (only the `custom_build_cmd` escape hatch
honored it before the fix, in
`src/dftracer_agents/mcp_tools/tools/session/session_tools.py`). See
[[dftracer-annotation-lessons]] LESSONS_LOG.md.

## Related

- [[workload-ygm-bench]] — LLNL's ygm-bench (github.com/llnl/ygm-bench), the
  traced benchmark that exercises this annotated tree: build steps, hot-loop
  over-annotation + selective-aggregation fix, MPI_Iallreduce rank-density
  collapse, irecv buffer OOM tuning, confirmed scaled-run config.
- [[software-mpi]] — MPI-IO/ROMIO, Cray MPICH ABI-matching details.
- [[system-tuolumne]] — Cray-clang20/spdlog-fmt compiler pitfall, PrgEnv-gnu recipe.
- [[tools-dftracer]] — prebuilt-vs-source dftracer decision, czgitlab source URL.
