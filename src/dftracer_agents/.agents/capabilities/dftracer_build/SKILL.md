---
name: dftracer_build
description: Detect an application's build system and features (session_detect), configure and build the ORIGINAL source with cmake, autotools, meson or pip (session_configure, session_build_install), copy the annotated tree and patch its build so it links dftracer (session_copy_annotated, session_patch_build), build the annotated tree (session_build_annotated), report annotation coverage (session_annotation_report), and validate either build with dftracer_build_check. Load for pipeline stages "build app" and "build annotated", or on "Unknown build tool", "fatal error: dftracer/dftracer.h: No such file or directory", "undefined reference to dftracer_*" / "initialize_main", "libdftracer_core.so: cannot open shared object file", ".deps/...Po:1: *** missing separator", "submodule is not present", an unrecognized -D flag silently ignored, or an annotated build that fails on a dftracer macro.
---

# dftracer_build

Converted from dftracer-agents' build tools, the `dftracer-build-app` and `dftracer-build-run` skills, the
`dftracer-build-app` dispatcher, the four `dftracer-build-system-{cmake,autotools,meson,pip}` agents, the build half of
`dftracer-build-smoke`, and the build/link entries of `dftracer-pitfalls`.

## Rules
- Build the ORIGINAL application only in the "build app" step; do not install dftracer there. Stay on the build: hand
  annotation problems back to the annotation stage instead of widening the build.
- Tool-first: `session_detect` -> dispatch to EXACTLY ONE build-system recipe by the detected `build_tool`, never more
  than one and never combined:
  - `cmake` -> read the project's `CMakeLists.txt` `option(...)` declarations first (an unrecognized `-D` is silently
    ignored) -> `session_configure(extra_cmake_flags=...)` (`cmake -S source -B build -DCMAKE_INSTALL_PREFIX=install
    -DCMAKE_BUILD_TYPE=RelWithDebInfo <flags>`) -> `session_build_install` (`make -j<jobs>` + `make install`).
  - `autotools` -> read `configure.ac` `AC_ARG_WITH`/`AC_ARG_ENABLE` first (unknown flags usually error loudly) ->
    `session_configure(extra_configure_flags=...)` (`autoreconf -fi` only if configure.ac exists and configure does
    not, then `./configure --prefix=install --disable-dependency-tracking <flags>`) -> `session_build_install`.
  - `meson` -> read `meson.options` (or legacy `meson_options.txt`) first (unknown `-D` silently ignored) ->
    `session_configure(extra_meson_flags=...)` (`meson setup build source -Dprefix=install <flags>`) ->
    `session_build_install` (`meson compile -C build -j<jobs>` + `meson install -C build`).
  - `python` (pip/pyproject: setuptools, hatchling, mesonpy, any PEP 517 backend) -> read
    `[build-system].build-backend`; `mesonpy` forwards meson options via
    `extra_pip_flags='--config-settings=setup-args="-Doption=value"'` -> `session_configure(extra_pip_flags=...)`
    (venv at install/, `pip install -e source/ <flags>`; this IS the install) -> `session_build_install` anyway (no-op,
    keeps the step sequence and records app_built). A pyproject with `build-backend = "mesonpy"` may be detected as
    `python`: route it to the pip recipe.
  - anything else (`make`, unknown) -> session_configure/session_build_install directly as a fallback, and propose a
    new build-system recipe + `software_<tool>` capability if it recurs.
  Then `session_status` / `session_get_run_paths`, and `dftracer_build_check(run_id, stage="original")`.
- Annotated build: precondition - the annotated tree passed `validate_annotations` for every language present
  (building an unvalidated tree wastes a compile and can produce a binary whose trace has no I/O). Patch the build
  system BEFORE annotating: `session_copy_annotated` -> (autotools: `session_generate_dftracer_pc`) ->
  `session_patch_build` (adds `-I<dftracer>/include`, `-L<lib> -Wl,-rpath,<lib>`, `-ldftracer_core`) ->
  `session_build_annotated(run_id, extra_cmake_flags=<same as the original>)`. Then
  `dftracer_build_check(run_id, stage="annotated")` and `session_annotation_report(run_id)`.
- A build failure naming a specific function in the annotated build is an ANNOTATION bug: report function + file and
  hand back to the annotator; do not edit source here. Max 2 retries, then escalate.
- Capture the FIRST compiler error, not the cascade. Prefer fixing the build-system source of truth over ad hoc shell.
- Manual annotation-build-run loop (when the pipeline exhausted its 3 retries or you work interactively): read
  `artifacts/10_session_build_annotated*.log` -> for each failing file `session_read_file` -> find the bad insertion
  (wrong scope, inside a struct, missing START) -> `session_write_file` the corrected COMPLETE file -> rebuild ->
  `session_run_with_dftracer` -> on a runtime crash in annotated code remove the offending macro, rebuild, rerun ->
  `session_split_traces` -> `session_analyze_traces`. If the annotate tool fails on the same file twice, hand-annotate
  that file with read/write. Coverage: every non-trivial C function `DFTRACER_C_FUNCTION_START()` as its first
  statement, `DFTRACER_C_FUNCTION_END()` before every return, `UPDATE_STR` for const char* params, `UPDATE_INT` for
  size/offset/fd; no REGION macros; C++ `DFTRACER_CPP_FUNCTION()` (RAII) + `_UPDATE`. A file with zero START calls was
  skipped or stripped during error recovery - re-annotate it.
- Every log under `<WS>/artifacts/`. Use `session_get_run_paths`, never hand-built paths.

## Constraints
- Session workspace only: `source/`, `build/`, `install/` for the original; `annotated/`, `build_ann/`, `install_ann/`
  for the annotated build. Never mutate annotated or trace directories while building the original.
- Never sudo; userspace prefixes only. Never edit an agent's annotated source to make a build pass.
- If a build tool fix needs a server restart, stop and name the tool; do not reproduce it by hand.

## Corner cases
- Autotools apps (IOR): `session_build_annotated` expects `annotated/configure` to exist and cannot express flags like
  `--with-hdf5=yes` + CPPFLAGS/LDFLAGS/LIBS. Use `custom_build_cmd` to drive `autoreconf -fi -I config` + `./configure`
  + `make`/`make install`, and persist it as an idempotent `<ws>/annotated/scripts/build.sh` that sources
  `scripts/env.sh` and APPENDS session-local LD_LIBRARY_PATH entries; later steps re-run that script.
- Custom recipes (Flash-X `bash setup ... && make -j`): pass `custom_build_cmd` (and `build_subdir`); an unsupported
  build tool otherwise errors.
- Meson reads CFLAGS/LDFLAGS only at the first `meson setup`: the annotated build sets PKG_CONFIG_PATH, CFLAGS
  (`-I<prefix>/include`) and LDFLAGS (`-L.../lib64 -L.../lib -Wl,-rpath,... -ldftracer_core`) unconditionally.
- `.deps/IOR-aiori-DUMMY.Po:1: *** missing separator` after a Makefile patch: `rm -rf .deps src/.deps && make clean &&
  make -j4`. For autotools patch `src/Makefile`, not only the top level.
- `fatal error: dftracer/dftracer.h: No such file or directory` + `undefined reference to 'initialize_main'`: the build
  was not patched before annotating; add the include dir, `-L... -Wl,-rpath,...`, and `-ldftracer_core -lcpp-logger
  -lbrahma`, then `rm -rf .deps src/.deps && make -j4`. The header belongs in each annotated .c/.cpp, never a .h.
- `undefined reference to 'dftracer_init'`: `LDFLAGS += -L$(DFTRACER_PREFIX)/lib -Wl,-rpath,$(DFTRACER_PREFIX)/lib`,
  `LIBS += -ldftracer_core`; for a pip/venv dftracer locate the lib dir with importlib (`find_spec('dftracer')`).
- Binary links but `libdftracer_core.so: cannot open shared object file` at run: add `-Wl,-rpath` or export
  LD_LIBRARY_PATH to the dftracer lib dir.
- Fortran linkers (`crayftn`, `mpif90`): the constructor/destructor wrapper `.o` must be on the link line and the CCE
  runtime libs on LD_LIBRARY_PATH.
- Submodule-vendored deps (RAJAPerf) need the recursive submodule update session_create performs.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
For each failed check of `dftracer_build_check`:
- `workspace` -> wrong run_id. `stage` -> pass 'original' or 'annotated'.
- `detected` -> `session_detect(run_id, ...)`; pin hdf5_prefix/mpicc/mpicxx when the app uses a session HDF5 or a
  specific MPI.
- `app_built` / `install_tree` / `build_log` -> re-run session_configure + session_build_install with the flags read
  from the project's option declarations; read the FIRST error in artifacts/*session_build_install*.
- `dftracer_installed` -> dftracer_install first.
- `annotated_source` -> the annotation stage has not run or was reset: annotate + validate_annotations first.
- `annotated_built` -> `session_build_annotated` (autotools: `custom_build_cmd` or the persisted build.sh).
- `links_dftracer` -> `session_patch_build` (and `session_generate_dftracer_pc` for autotools), rebuild with rpath.
Then predict (the build tool) and validate again; the same failure twice stops for the user.

## Self-heal
- A build tool that is wrong for a whole class of projects (configure, build_install, build_annotated, patch_build) is
  fixed here (capability_update) and the server restarted; a single project's quirk goes to its workload capability.

## Self-learn
- What is explored here becomes a capability: a CMake/autotools/meson/pip quirk tagged (system, dependency) becomes or
  extends `software_cmake` / `software_autotools` / `software_meson` / `software_pip`; a project's own option names or
  broken targets go to `workload_<app>` / `software_<app>`; do not generalize one system's finding into "CMake always
  does X". See capability_builder.

## What works
- CMake apps: `session_detect` -> read option(...) -> `session_configure(extra_cmake_flags)` -> `session_build_install`.
- IOR (autotools, annotated): `custom_build_cmd` running `autoreconf -fi -I config && ./configure --with-hdf5=yes ... &&
  make && make install`, persisted as `annotated/scripts/build.sh` sourcing `scripts/env.sh`.
- Annotated autotools build: `session_install_dftracer` -> `session_generate_dftracer_pc` -> `session_patch_build` ->
  `session_build_annotated` (PKG_CONFIG_PATH and --disable-dependency-tracking set by the tool).
