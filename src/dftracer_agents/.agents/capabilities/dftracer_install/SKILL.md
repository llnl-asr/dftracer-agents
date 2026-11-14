---
name: dftracer_install
description: Install dftracer into a dftracer session AFTER the original app built - by default the dftracer pre-release with dftracer-modules (binary wheels) then dftracer-compose linking only the asked-for layers (mpi, hdf5, papi, hip, hwloc, cuda) against the application's own MPI/HDF5/ROCm in the session env.sh; or a pip/source build - plus dftracer_wheelhouse (search/retire prebuilt wheels), session_install_dftracer_utils, session_generate_dftracer_pc (pkg-config for autotools) and the validator dftracer_install_check. Load before session_install_dftracer, or on "Build the original app first", "dftracer-compose did not link", "CMake 3.24 or higher is required", "GLIBCXX_3.4.30 not found", "DFTracer was enabled but not available", "NoOpProfiler", "double free or corruption (!prev)", "libmodules.so.1: cannot open shared object file", "zlib_name_mangling.h file not found", two libdftracer_core copies, or empty traces after a clean install.
---

# dftracer_install

Converted from dftracer-agents' install tools and the `dftracer-install`, `dftracer-build-dftracer` skills and agent.
Install dftracer for the session and stop.

## Rules
- App first, then dftracer (MANDATORY ORDER). Never install dftracer before the ORIGINAL app built: dftracer is
  composed (or built) against what that build resolved - same CC/CXX, same MPI, same venv, the same HDF5/ROCm (only
  if the user wants those layers). `session_install_dftracer` errors until `app_built` is recorded; pass
  `app_built_override=True` only when the app was built outside `session_build_install`.
- Tool-first: `session_detect` (pin HDF5/MPI first) -> `session_install_dftracer` -> `session_get_run_paths` ->
  `session_status`; then `dftracer_install_check(run_id, require=<layers>)`. If the tool fails, decide whether the tool
  or a rule/lesson needs updating, and record it at once. Skipping the tool for a manual install defeats the agent.
- Default `prebuilt="compose"`: `pip install --pre "dftracer[modules]" dftracer-modules` (the pre-release, binary
  wheels, seconds) into `<WS>/venv`, then `dftracer-compose --enable <asked> --disable <rest>` inside the session's
  `env.sh` (kept as `scripts/compose_dftracer.sh`, log `artifacts/06c_compose_dftracer.log`). Read the result's
  `compose`: `linked` is what dftracer has, `absent` a library this machine does not have (load its module, re-run
  `scripts/compose_dftracer.sh`); a layer that did not build fails the tool. Enable ONLY the layers the user asked for
  (`mpi=True`, `papi=False`, `hip=False`, `variorum=False`, `hdf5=False` ...). A source build (`prebuilt="auto"`) only
  when the user asks for one or for a branch that is not a release; a specific PyPI version or
  `git+https://github.com/llnl-asr/dftracer.git@develop` is still `pip install`, never a manual clone + cmake.
- Pin the app's HDF5 + MPI BEFORE installing: call `session_detect(run_id, hdf5_prefix=<WS>/hdf5_1.14,
  mpicc=..., mpicxx=...)` at least ONCE (right after building HDF5 / resolving MPI). Since 2026-07-10
  `session_install_dftracer` re-runs detection itself and carries the pinned values forward, but an unpinned first
  detect (auto-probed /usr) is re-confirmed forever. dftracer must link the SAME HDF5 and MPI as the app or HDF5/POSIX
  interception silently records nothing (soname mismatch); never let detection pick a stray `/usr` HDF5 1.10.5.
- Re-run `session_detect` with explicit MPI wrappers after the module stack is finalized (e.g. cce/21.0.0 +
  cray-mpich/9.1.0 found in STEP 1): source `$WS/scripts/env.sh`, then `session_detect(run_id, mpicc=..., mpicxx=...)`.
- RULE 0 - a consistent env is mandatory (install env == run env). Read the app's own scripts first
  (`<app>/scripts/install-<system>.sh`, `<app>/scripts/<app>-<system>.job`, `pyproject.toml` extras) and reuse VERBATIM
  for install and run: python version, module set, `LD_PRELOAD`, `LD_LIBRARY_PATH`, patchelf fixups. Install dftracer
  in the SAME script and the SAME venv as the app (critical for DL workloads: torch/mpi4py wheels pin an exact
  MPI/ROCm/Python ABI). Never take modules from system_detect defaults, README prose or a previous session.
- RULE 1 - pass MPI (and HDF5 only if the app uses it) explicitly; dftracer's setup.py reads ENV VARS, not CMAKE_ARGS.
  Bind CC/CXX to the MPI THE APP USES (`export MPICC=$MPI_ROOT/bin/mpicc MPICXX=... CC=$MPICC CXX=$MPICXX
  DFTRACER_ENABLE_MPI=ON DFTRACER_BUILD_WITH_MPI=ON`; HDF5: `DFTRACER_ENABLE_HDF5=ON HDF5_ROOT=<prefix>
  HDF5_DIR=<prefix>`; ROCm: `ROCM_PATH`, `CMAKE_PREFIX_PATH="$ROCM_PATH:$ROCM_PATH/lib/cmake:..."`,
  `rocprofiler_sdk_DIR=$ROCM_PATH/lib/cmake/rocprofiler-sdk`, or rocprofiler-sdk is silently skipped).
- RULE 2 - LD_LIBRARY_PATH: include `/usr/lib64` (libdl) and `export LDFLAGS="-ldl"` (single token, no trailing space:
  CMake CMP0004); at RUN time also the CCE runtime libs when built with crayclang, and
  `<venv>/lib/pythonX.Y/site-packages/torch/lib` for torch (dftracer's gotcha intercepts dlopen and defeats torch's
  $ORIGIN RPATH for libcaffe2_nvrtc.so).
- RULE 3 - install ORDER: dftracer BEFORE dftracer-utils (utils installs headers into site-packages/dftracer/include/;
  a stale zconf.h breaks dftracer's own build). No `--no-build-isolation` (needs setuptools_scm). `set -o pipefail` so
  `pip ... | tee` cannot hide a failure.
- RULE 4 - a user `~/.pip/pip.conf` can hide the site wheel index (e.g. `torch==2.10.0+rocm710` from `.[rocmwci]`):
  force `PIP_INDEX_URL`/`PIP_TRUSTED_HOST`; never fall back to a generic extra (`.[rocm]`), which changes the ABI.
- RULE 5 - initialize once per process: a dispatcher importing every subcommand module that each call
  `initialize_log()` double-initializes and `finalize()` double-frees; init in one module, `dftracer.get_instance()`
  elsewhere.
- Verify all THREE packages independently (never assume one implies another): `python -c "import dftracer"` (C core),
  `python -c "from dftracer.python import dftracer, dft_fn"` (pydftracer, needed to annotate Python), dftracer-utils
  (imports as `dftracer.utils`; trace-utils CLI). `pip show dftracer` listing pydftracer under Requires proves nothing.
  If pydftracer is missing, install it into the SESSION venv - never repair the shared framework venv in place.
- Verify, never assume: `python -c "import dftracer.dftracer"` (an ImportError is swallowed by logger.py -> NoOpProfiler
  -> exit 0 with an EMPTY trace); `ldd <site-packages>/dftracer/lib64/libdftracer_core.so | grep -i mpi` shows exactly
  ONE libmpi; for DL `python -c "import dftracer.dftracer, torch; torch.cuda.init()"`; then a real run with a NON-EMPTY
  `.pfw`. A zero exit code does not mean tracing worked.
- Python/pip: never bare `python`/`pip`; always the full venv path (`<WS>/venv/bin/python`, `<WS>/venv/bin/pip`), and
  `sys.executable` / the venv bin path inside MCP tool code.
- Install and privilege: never `sudo` or system paths. pip -> the venv pip; CMake `-DCMAKE_INSTALL_PREFIX=<userspace>`;
  autotools `--prefix=<userspace>`; npm `--prefix ~/.local`; conda into the active user env; binaries to `~/.local/bin`
  or the workspace. Override a `/usr/local` default instead of elevating.
- Autotools + dftracer: dftracer is CMake-only and ships no dftracer.pc. Order: `session_install_dftracer` ->
  `session_generate_dftracer_pc` (install_ann/lib/pkgconfig/dftracer.pc) -> `session_patch_build` ->
  `session_build_annotated` (sets PKG_CONFIG_PATH and --disable-dependency-tracking). Never write Make `:=` syntax in
  bash (`DFTRACER_INC := ...` -> "command not found"). Add `--disable-dependency-tracking` to every `./configure` and
  clean stale `.deps` (`find <dir> -name .deps -type d -exec rm -rf {} +`) before reconfiguring.
- HDF5 compatible versions: 1.8.23, 1.10.5, 1.12.3, 1.14.5 (preferred, default). Any other series (1.6.x, 1.16.x) is
  unsupported: build 1.14.5 from source into `<WS>/hdf5_1.14` (`CC=mpicc ./configure --prefix=<WS>/hdf5_1.14
  --enable-parallel --enable-shared --enable-build-mode=production --with-zlib=/usr`; `make -j && make install`;
  export HDF5_DIR and LD_LIBRARY_PATH), then rebuild the app with `-I$HDF5_DIR/include -L$HDF5_DIR/lib -lhdf5`.
  1.14.x unlocks `H5Pset_page_buffer_size` with MPI-IO, the async VOL (`H5Fcreate_async`) and collective metadata flush.
- `dftracer_wheelhouse(action="search", system=..., python_tag=...)` lists prebuilt wheels per exact build combination
  before a source build; `action="retire", key=...` marks a bad wheel broken so it is never installed again.

## Constraints
- Session workspace only (its install and build dirs); never modify unrelated source trees; never sudo.
- Keep install logic in the MCP tool unless the tool itself is wrong; prefer fixing the tool or its assumptions over a
  manual workaround. If a fix needs a server restart, STOP and say which tool - never reproduce it by hand meanwhile.
- Propose new pitfalls and the working recipe to the main thread (redacted); never self-write skills or lessons.

## Corner cases
- dftracer-compose needs cmake >= 3.24 ("CMake 3.24 or higher is required" at CMakeLists.txt:38 with a system 3.23.1):
  put the venv's cmake first on PATH; run `dftracer-compose --check` first (changes nothing), then the real run.
- A composition linking hwloc/PAPI/the default MPI pulls system libraries into libdftracer_core; under an anaconda
  python `import dftracer.dftracer` can then fail with "GLIBCXX_3.4.30 not found (required by .../libicuuc.so.73)"
  and pydftracer prints "DFTracer was enabled but not available" (decorators log 0 events): preload the python's newer
  libstdc++.so.6 for the python and the service start/stop, or compose fewer layers; re-test the import after any change.
- Cray MPICH 9.0.1 IS compatible for MPI-IO tracing on tuolumne (user-confirmed 2026-07-08): pass the MPI version and
  include headers (GNU 11.2 wrappers, include dir under the cray-mpich ofi/gnu tree); if the build auto-disables MPI-IO,
  override the version gate rather than accepting PRELOAD-POSIX-only.
- Session-built HDF5 1.14.5 + Cray MPICH GNU wrappers: pass hdf5_prefix AND mpicc/mpicxx explicitly to session_detect,
  or detection mixes system HDF5 with session MPI.
- CPU-only workloads may set `DFTRACER_ENABLE_HIP_TRACING=OFF` (no ROCm deps); no impact on I/O tracing.
- Tuolumne: the CCE libs must be on LD_LIBRARY_PATH before session_install_dftracer
  (`/opt/cray/pe/cce/<ver>/cce/x86_64/lib`, `.../lib/default64`, `/usr/lib64`) or the link fails with
  `--no-allow-shlib-undefined` (system_detect env normally applies it - verify).
- A from-scratch CMake dftracer build ALWAYS needs two configure passes: pass 1 `-DDFTRACER_INSTALL_DEPENDENCIES=ON` +
  `cmake --build` only fetches/builds cpp-logger, brahma, gotcha, libuv (prints "downloading dependencies. Please run
  make ... reconfigure without dependency flag", exits 0 with no dftracer_core compile); pass 2 reconfigure the SAME
  build dir with `=OFF`, then build + install. A single pass ships stale or missing dftracer_core/_service.
- On Cray clang `-static-libstdc++` is a silent no-op ("argument unused during compilation"): use
  `-nostdlib++ <path-to-libstdc++.a> -lm` in CMAKE_SHARED_LINKER_FLAGS / CMAKE_EXE_LINKER_FLAGS (e.g. the gcc-toolset-13
  libstdc++.a) when the binary must not depend on the system's old libstdc++.so.6.
- `double free or corruption (!prev)` at exit: two MPI runtimes (dftracer linked libmpi_cray, the app preloads
  libmpi_gnu) or two OpenMP runtimes - rebuild/compose with the app's MPI wrappers.
- `HIP Intercept context start failed` / `Error in dlopen: libcaffe2_nvrtc.so`: wrong/missing ROCm module or libomp
  preload - NOT a broken ROCProfiler; do not disable it.
- A `.dev` dftracer in a shared venv had a working core but no pydftracer: verify all three packages.
- `session_generate_dftracer_pc` finds libdftracer_core in install_ann/lib[64] or the pip site-packages layout and
  defaults the version to 2.0.3 when no versioned soname exists.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
For each failed check of `dftracer_install_check`:
- `install_recorded` -> run `session_install_dftracer(run_id, ...)`; read its `error`, `steps`, `full_log`.
- `app_built_first` -> build the original app (dftracer_build: session_build_install), then reinstall/compose.
- `core_library` / `header` -> reinstall into the SESSION venv (pre-release); dftracer before dftracer-utils (RULE 3).
- `one_core` -> an old wheel (dftracer.libs copy) or a source build over a wheel: reinstall the pre-release fresh and
  compose (compose links libdftracer_core.so to the one library).
- `composed` -> re-run `scripts/compose_dftracer.sh` in the session env.sh with cmake >= 3.24 first on PATH.
- `linked_<layer>` -> 'missing': the layer failed to build (read artifacts/06c_compose_dftracer.log, fix the module);
  'absent': this machine has no such library - load the app's module or ask the user to drop the layer.
- `pkgconfig` -> `session_generate_dftracer_pc(run_id)`.
- `imports` -> GLIBCXX: libstdc++ preload or fewer layers; wrong python: install env != run env (RULE 0).
Then predict (session_install_dftracer / compose) and validate again; the same failure twice stops for the user.

## Self-heal
- If session_install_dftracer itself is wrong, fix it here (capability_update) and ask for a restart; record the
  pitfall the moment it appears so later agents inherit the fix.

## Self-learn
- What is explored here becomes a capability: a library quirk (HDF5, MPI, ROCm, compilers) becomes or extends
  `software_<lib>`, a site quirk `system_<machine>`, an app's linkage caveat `workload_<app>`. See capability_builder.
  Leave a clear trail for the next session in LESSONS.md.

## What works
- 2026-11-13 (tuolumne): pre-release `dftracer[modules]` + dftracer-modules, composed with Cray MPICH 9.0 and HDF5 1.14
  (`dftracer-compose --enable mpi,hdf5 --disable papi,hwloc,hip,cuda`); one libdftracer_core; composition.txt lists
  mpi and hdf5 linked.
- 2026-07-10 (h5bench): pinned `session_detect(hdf5_prefix=<WS>/hdf5_1.14, mpicc, mpicxx)` before install fixed a
  brahma HDF5-async ABI mismatch and disabled MPI-IO interception.
- 2026-07-14 (vpic-kokkos): cmake dftracer rebuild with the mandatory two configure passes.
- Autotools apps: install -> session_generate_dftracer_pc -> session_patch_build -> session_build_annotated.
