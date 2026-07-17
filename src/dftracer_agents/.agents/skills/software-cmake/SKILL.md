---
name: software-cmake
description: CMake build system detection, configure/build/install flow via session_configure/session_build_install/session_build_annotated, CMAKE_PREFIX_PATH dftracer injection, and per-system/per-dependency lesson tagging. Load this skill for any project whose top level is CMakeLists.txt.
metadata:
  type: software
---

## Detection

- Top-level `CMakeLists.txt` with `project(<name> ...)`. Check for a
  `cmake_minimum_required(VERSION ...)` line to confirm the minimum version.
- Look for `option(...)` declarations for project-specific toggles before
  calling `session_configure` — an unrecognized `-D` flag name is silently
  ignored by CMake rather than erroring, so read the options first.
- `find_package(<name> ...)` / `find_library(...)` calls determine how a
  dependency is located — CONFIG mode (a `<Name>Config.cmake` file) vs MODULE
  mode (a `Find<Name>.cmake` script) vs pkg-config via
  `pkg_check_modules(...)`. Check which one dftracer or another dependency
  needs BEFORE assuming `-DCMAKE_PREFIX_PATH=<prefix>` alone is sufficient —
  dftracer currently ships CMake config files but no `.pc` file, so
  `find_package(dftracer CONFIG)` works but `pkg_check_modules(dftracer)`
  does not.

## Configure / build / install flow (via MCP tools)

```
session_configure(run_id, extra_cmake_flags="-DENABLE_TESTS=OFF ...")
session_build_install(run_id, jobs=<n>)
```

or for the annotated tree:

```
session_build_annotated(run_id, extra_cmake_flags="...")
```

- `session_configure` runs `cmake -S source -B build -DCMAKE_INSTALL_PREFIX=install
  -DCMAKE_BUILD_TYPE=RelWithDebInfo <extra_cmake_flags>`.
- `session_build_annotated` automatically appends
  `-DCMAKE_PREFIX_PATH=<dftracer_install_prefix>` when
  `session_install_dftracer` was already called — you do not need to pass
  this yourself.
- `session_build_install` runs `make -j<jobs>` then `make install` inside
  `build/` (or `build_ann/` for the annotated tree) — this project uses the
  Makefile generator, not Ninja, by default.
- Re-running `session_configure` on an existing build dir reconfigures in
  place; no need to delete `build/` first unless CMake's cache is corrupted
  (symptom: stale `CMakeCache.txt` referencing a deleted compiler/prefix —
  fix by deleting `build/CMakeCache.txt` specifically, not the whole tree).

## Lessons are per-system and per-dependency, not universal

CMake itself is portable, but whether `find_package` resolves a dependency
via CONFIG or MODULE mode, which system paths are searched, and what a
missing dependency's exact error looks like varies by system and by the
dependency itself. Tag every lesson below with the `(system, dependency)`
pair it was observed on. Cross-link the system-specific half into the
relevant `system-<system>` skill when it's about the site rather than CMake
itself.

## Lessons

(none recorded yet for this repo's sessions beyond what's captured per
`workload-<app>` skills for specific projects — e.g. h5bench's CMake quirks
live in `workload-h5bench`. Record a NEW cross-project CMake lesson here;
keep project-specific CMakeLists.txt quirks in that project's own skill.)
