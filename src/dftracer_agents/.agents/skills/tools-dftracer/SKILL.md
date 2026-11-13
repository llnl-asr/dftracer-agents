---
name: tools-dftracer
description: The dftracer PyPI package itself (C core + CMake build) — package layout, dependency graph (dftracer-utils, pybind11, pydftracer), install sources (the prerelease composed for the run by default, source builds via pip), and session-local vs shared-venv install rules. Load this skill before any session_install_dftracer call or before diagnosing a "dftracer works but X sub-feature is missing" report.
metadata:
  type: software
---

## What "dftracer" actually is

`dftracer` on PyPI (https://pypi.org/project/dftracer/) is the C-core I/O
profiler package. `pip show dftracer` declares:
```
Requires: dftracer-utils, pybind11, pydftracer, setuptools, setuptools-scm
```
So a CORRECT install pulls in three related-but-separate packages:
- **dftracer-utils** — Python bindings for the trace-utils CLI (split/merge/etc.). See [[tools-dftracer-utils]].
- **pydftracer** — the Python annotation API (`dftracer.python`: `dftracer.initialize_log`, `dft_fn`, `dftracer_fn` decorators). See [[tools-pydftracer]].
- **pybind11** — build-time dependency for the C-extension bindings.

## Install sources

**Default (2026-11): the dftracer prerelease, composed for the run.** dftracer's develop publishes a prerelease
to PyPI with a second wheel, `dftracer-modules`: its optional modules (MPI for every common MPI and Cray MPICH,
HDF5 serial and parallel, PAPI, hwloc, ROCm's rocprofiler-sdk, CUDA's CUPTI) prebuilt per library version.
`dftracer-compose` links the ones this machine and run need into the installed `libdftracer_core`, against
the libraries the loaded modules provide, and builds from the sources the wheel ships any variant that is not
prebuilt. `session_install_dftracer` does all of it by default (`prebuilt="compose"`): binary wheels only,
then compose inside the session's `env.sh` for the features detection (or the `mpi`/`hdf5`/`papi`/`hip`
arguments) asks for -- seconds to a minute or two instead of a source build, with the same features.
By hand, in the app's venv with its modules loaded:

```bash
/path/to/venv/bin/pip install --pre "dftracer[modules]" dftracer-modules
/path/to/venv/bin/dftracer-compose --enable mpi,hdf5 --disable papi,hwloc,hip,cuda   # what the run needs
/path/to/venv/bin/dftracer_install_info --composition                                # what is linked
```

Verify what was linked, never what was asked: the tool result's `compose` (`linked`, and `absent` for a
library this machine does not have -- load its module in `env.sh`, then re-run
`scripts/compose_dftracer.sh`), or `dftracer_install_info --composition`. A module that did not build fails
the tool. Never the plain prerelease wheel alone (`prebuilt="prerelease"`): uncomposed, it traces POSIX,
STDIO and annotations only -- the 2026-08-04 YGM session found exactly that (`ldd` showed no MPI), which is
why the rule was once "always build from source" (see [[feedback-never-prebuilt-when-config-knobs-needed]]).
Variorum needs no compose: `dftracer_service` loads `libvariorum.so` (0.8 or later) when it starts; name it
with `DFTRACER_VARIORUM_LIBRARY` when the system's own Variorum (TOSS 4 has one in `/lib`) is not the one
built for the machine.

**Source builds** (`prebuilt="auto"`, cached in the wheelhouse): only when the user asks for one or for a
dftracer branch or fork that is not a release. `pip install "git+https://github.com/llnl-asr/dftracer.git@<ref>"`
drives the same CMake build; `session_install_dftracer(prebuilt="auto", dftracer_ref=..., dftracer_repo=...)`
does it with the CMake two-pass bootstrap and the `DFTRACER_ENABLE_*`/`MPICC`/`MPICXX`/`HDF5_ROOT` env vars set
(RULE 1 in `dftracer-install`); never a manual clone + cmake. The canonical repos are under `llnl-asr` on
GitHub (`dftracer`, `dftracer-utils`, `pydftracer`, dfanalyzer, dfdiagnoser); install `dftracer` before
`dftracer-utils` (RULE 3 in `dftracer-install`). Verify a source build in what was installed, not in the
tool's response: `grep -E "PAPI_TRACING_ENABLE|MPI_ENABLE|HIP_TRACING_ENABLE"
<prefix>/include/dftracer/core/dftracer_config.hpp`, and `ldd <prefix>/lib64/libdftracer_core.so | grep -i mpi`
must show exactly one `libmpi`. A source build with Variorum links `libvariorum` into `dftracer_service`
only, never into `libdftracer_core.so` or the traced app
([[bug-dftracer-variorum-linked-into-app-heap-corruption]]). Reinstalling over a previous dftracer leaves a
stale cmake/brahma tree: remove the session venv first (`session_remove_path(relpath="venv", recursive=True)`)
-- see [[bug-dftracer-stale-brahma-after-pip-uninstall]].

## Source builds: CC/CXX/LD_LIBRARY_PATH MUST be set before pip install (this always fails otherwise)

Applies to source builds only; the default composed install compiles nothing unless a module has no prebuilt variant, and then dftracer-compose builds it in the session env.

`pip install dftracer` (from PyPI OR from the GitHub develop git URL) compiles
a C/C++ extension under the hood — it is NOT a pure-Python wheel install. This
reliably fails or silently produces a broken/mismatched build if the
environment isn't set BEFORE invoking pip:

```bash
export CC=$(which mpicc)      # or the plain C compiler if the app has no MPI
export CXX=$(which mpic++)    # or mpicxx / the plain C++ compiler
export LD_LIBRARY_PATH="<compiler-runtime-lib-dirs>:$LD_LIBRARY_PATH"
pip install dftracer   # or pip install "git+https://github.com/llnl-asr/dftracer.git@develop"
```

Also export dftracer's own CMake feature-flag env vars BEFORE pip install —
they are read during the C-extension build, not after: e.g.
`DFTRACER_ENABLE_PYTHON=ON` (needed for `pydftracer` to actually build/install
its bindings — confirmed required 2026-07-16), `DFTRACER_ENABLE_HDF5=ON` +
`HDF5_ROOT`/`HDF5_DIR` (only if the app uses HDF5), `DFTRACER_BUILD_TYPE=...`.
See `dftracer-install-env-vars` for the full flag list.

Do this EVERY time, not just once per system — a fresh shell/session does not
inherit a previous session's exports. Symptoms of skipping this step:
undefined-symbol errors at import time, a build that "succeeds" but links
against the wrong libstdc++/MPI ABI (crashes at exit with `double free or
corruption` when two MPI runtimes coexist), or `libdl`/`dlopen` link failures
on Cray PE (`/usr/lib64` must be on `LD_LIBRARY_PATH` in addition to the
compiler's own runtime lib dirs). See `dftracer-build-dftracer`'s own pitfalls
for the full CC/CXX/LD_LIBRARY_PATH recipe (bind to the SAME compiler/MPI the
traced app itself uses, never a different one) — this skill exists so the
requirement is visible from the package-selection angle too, not just buried
in the build-agent's install steps.

## Known bug: an existing venv can have dftracer without pydftracer

**Symptom:** `import dftracer` works, `pip show dftracer` lists `pydftracer` as
a `Requires:` dependency, but `from dftracer.python import dftracer, dft_fn`
raises `ModuleNotFoundError: No module named 'dftracer.python'`, and
`pip show pydftracer` reports "Package(s) not found".

**Root cause (observed on the framework's own shared venv,
`$PROJECT_ROOT/.venv`, `dftracer==2.0.3.dev54`):** a
`.dev` version number strongly suggests an editable/local (`pip install -e`)
or otherwise non-standard install that didn't fully resolve its own dependency
graph — a plain metadata `Requires:` entry does not guarantee pip actually
installed that dependency if the install path bypassed normal dependency
resolution (e.g. `--no-deps`, an editable install of a stale checkout, or a
build that only produced the C-extension without triggering the `pydftracer`
sub-package install).

**Fix:** never assume an existing `dftracer` install is complete just because
`import dftracer` succeeds — always verify with
`python -c "from dftracer.python import dftracer, dft_fn"` before building an
annotated Python app against it. If incomplete, do NOT patch the existing
(especially shared/framework) venv in place — install a fresh, complete
`dftracer` into the SESSION's own venv instead (see [[dftracer-build-dftracer]],
[[feedback_dftracer_aiml_venv]]).

## Runtime env var: DFTRACER_INC_METADATA (confirmed 2026-07-17)

`DFTRACER_INC_METADATA=1` — defined in
`dftracer/include/dftracer/core/common/constants.h:18` — controls whether
`int_args`/`string_args`/`float_args` (the tag/argument payload on a trace
event, e.g. C's `DFTRACER_*_FUNCTION_UPDATE_STR` or Python's `log_event(...,
int_args=..., string_args=...)`) get written into the trace AT ALL. Without
it, every event's `name`/`cat`/`start_time`/`duration` write correctly but the
entire `args` payload is silently dropped — indistinguishable at first glance
from a tag-naming bug or an annotation bug, since the process runs cleanly and
event counts/timing look right. Set this alongside the standard
`DFTRACER_ENABLE=1`/`DFTRACER_LOG_FILE`/`DFTRACER_DATA_DIR` triplet for every
run where per-event tags matter (which is most runs — `comp=` classification,
custom key/value context, node/rank grouping tags, etc. all depend on it).

## `pip uninstall dftracer` leaves a stale cmake-installed brahma tree behind

**Symptom:** upgrading an existing session venv's dftracer to a newer
`develop` fails at COMPILE time (not install time) with a wall of errors like:

```
src/dftracer/core/brahma/posix.h:231: error: only virtual member functions can be marked 'override'
  ssize_t readv(int fd, const struct iovec* iov, int iovcnt) override;
```

**Root cause:** `pip uninstall dftracer` removes only the files pip itself
recorded. dftracer's build ALSO cmake-installs its C dependencies straight
into the package prefix (`<venv>/lib/pythonX.Y/site-packages/dftracer/
{include,lib64,bin,etc,share}`), and those are left behind by a plain `pip
uninstall`. dftracer's `setup.py` then passes
`-DCMAKE_PREFIX_PATH=<that same prefix>` on the next install, so the NEW
build compiles against the OLD `brahma/interface/posix.h`. A newer dftracer
that overrides `readv`/`writev`/`pread64`/etc. crashes against the stale
brahma base class that never declared those virtuals — nothing in the error
mentions a stale dependency, so it reads like a compiler/toolchain problem
and easily burns a debug cycle down the wrong path.

**Fix:** before reinstalling or upgrading dftracer into an EXISTING venv,
delete the leftover cmake tree explicitly, then uninstall/reinstall normally:

```bash
rm -rf <venv>/lib/pythonX.Y/site-packages/dftracer <venv>/bin/dftracer_service
pip uninstall -y dftracer pydftracer dftracer-utils
```

Confirm the diagnosis first — on the stale copy,
`grep -c readv <venv>/.../site-packages/dftracer/include/brahma/interface/posix.h`
returns 0 (the old header has no `readv` declaration at all).

## Prebuilt prerelease wheel's packaged CMake config is broken for CMake C++ consumers (confirmed 2026-08-04, YGM; again 2026-11-13 on 2.2.0.post57.dev0)

Still true of the composed prerelease (2026-11-13, tuolumne, 2.2.0.post57.dev0 + dftracer-compose):
`find_package(dftracer CONFIG REQUIRED)` stops with "The imported target dftracer_core references the file
/project/build/lib.linux-x86_64-cpython-313/dftracer/lib64/libdftracer_core.so.4.3.0 ... does not exist", and
`dftracer-config.cmake` still sets `brahma_DIR=/opt/dftracer-deps/...` and requires brahma, which is now linked
statically into the core and not needed by a consumer. Compiling and linking against the install directly works
(`-I<pkg>/include -L<pkg>/lib64 -ldftracer_core`, as `session_build_annotated` and the generated `.pc` do), and
`dftracer/dftracer.h` no longer includes `<cpp-logger/logger.h>`, so the cpp-logger note below is out of date.

The prerelease distribution (see Install sources) ships
`lib64/cmake/dftracer/dftracer-config.cmake` + `dftracer-targets-release.cmake`,
but for a C++ project that does `find_package(dftracer CONFIG REQUIRED)`
against it, two real breakages surface:

1. `dftracer-targets-release.cmake` hardcodes the ORIGINAL BUILD MACHINE'S
   paths in `IMPORTED_LOCATION_RELEASE` (e.g. `/project/build/...`), not the
   installed venv location — `find_package` succeeds but the imported target
   points at a path that doesn't exist in the consuming session.
2. `dftracer-config.cmake` unconditionally `find_package(brahma REQUIRED)`,
   but `brahma` (only needed for PRELOAD-mode interception) is NOT packaged
   in this wheel at all — `find_package(dftracer)` fails outright with
   `brahma_DIR` not found, even for a FUNCTION-mode-only C++ consumer that
   never needed brahma.

**Workaround** (used successfully on YGM): skip `find_package(dftracer)`
entirely and define a manual `IMPORTED` CMake target pointing straight at the
wheel's `lib64/libdftracer_core.so` + `include/`:
```cmake
add_library(dftracer_core_imported SHARED IMPORTED)
set_target_properties(dftracer_core_imported PROPERTIES
  IMPORTED_LOCATION "${DFTRACER_ROOT}/lib64/libdftracer_core.so"
  INTERFACE_INCLUDE_DIRECTORIES "${DFTRACER_ROOT}/include;${CPP_LOGGER_INCLUDE_DIR}")
target_link_libraries(<your_target> PRIVATE dftracer_core_imported)
```
Also note: the wheel's `dftracer/dftracer.h` transitively requires
`<cpp-logger/logger.h>`, which this wheel does NOT package — the consuming
project must supply that include path separately (found via a sibling
`cpp-logger` source checkout in the YGM session; there may not always be one
available, this is a real packaging gap worth closing upstream).

This is a genuine wheel-packaging defect (stale absolute paths baked into a
release artifact + a spurious hard dependency), not a usage mistake — worth
fixing at the dftracer packaging level (regenerate the CMake config
relocatably at wheel-build time, make brahma optional) rather than expecting
every consumer to rediscover this workaround.

## Session-local vs shared venv (MANDATORY)

Never install or repair dftracer inside the shared framework venv
(`/usr/workspace/.../dftracer-agents/.venv`) as a side effect of a session's
build — that venv is the framework's OWN dependency environment, not a place
to fix up for a traced app. Always install a session-local dftracer (via
`session_install_dftracer`) and point the traced app's own build/run at that
session's install, per [[feedback_dftracer_aiml_venv]] (dftracer and the app
share ONE venv — the session's, not the framework's).

## Prebuilt prerelease wheel's CMake package is broken for CMake C++ consumers

**Symptom:** a CMake C++ project's `find_package(dftracer CONFIG REQUIRED)`
against the prerelease-wheel install (see "Prebuilt prerelease distribution"
above) fails, or configures but references stale paths.

**Root cause (confirmed on `llnl/ygm`, 2026-08-04):**
`<prefix>/lib64/cmake/dftracer/dftracer-targets-release.cmake` has hardcoded
`IMPORTED_LOCATION_RELEASE` paths from the WHEEL'S OWN BUILD MACHINE (e.g.
`/project/build/...`), not the install prefix the wheel actually landed in.
Separately, `dftracer-config.cmake` unconditionally `find_package(brahma
REQUIRED)`, but the wheel does not ship a `brahma` CMake package at all —
`brahma` is only needed for PRELOAD-mode interception, irrelevant to
FUNCTION-mode source annotation (the only mode this project uses, see
[[feedback-always-function-mode]]). The wheel also doesn't package
`cpp-logger` headers, which `dftracer/dftracer.h` transitively requires.

**Fix:** do not use `find_package(dftracer CONFIG)` against this prerelease
wheel. Define a manual `IMPORTED` target instead, pointed straight at the
`.so` and include dirs:

```cmake
add_library(dftracer_core_imported SHARED IMPORTED)
set_target_properties(dftracer_core_imported PROPERTIES
  IMPORTED_LOCATION "${DFTRACER_ROOT}/lib64/libdftracer_core.so"
  INTERFACE_INCLUDE_DIRECTORIES "${DFTRACER_ROOT}/include;${CPP_LOGGER_INCLUDE_DIR}"
)
target_link_libraries(<your_target> PRIVATE dftracer_core_imported)
```

Pass `-DDFTRACER_ROOT=<venv>/lib/pythonX.Y/site-packages/dftracer` (and locate
`cpp-logger` headers separately — on Tuolumne found at
`$HOME/dftracer-project/cpp-logger/include` as of this session; a
proper fix would have the wheel ship these itself). This is a packaging bug in
the prerelease wheel, not something to patch per-project each time — worth
fixing at the distribution level (ship correct `IMPORTED_LOCATION` paths, drop
the hard `brahma` requirement for FUNCTION-only consumers, package
`cpp-logger` headers).

---

## dftracer_service: two failure modes that leave a 0-byte trace and no error

Both were found on an Intel 2-socket / Slurm system while running the
node-counter service alongside every application launch. In both cases the
service *looks* like it started correctly — `start` returns 0, the pid file
appears — and the trace file is created but stays **0 bytes forever**.

### 1. variorum calls `exit()` and kills the whole daemon when the service is pinned to one core

The standing rule is to run one `dftracer_service` per node **pinned to a
single core** (`-N n -n n -c1`) so it does not compete with application ranks —
see `feedback-dftracer-service-node-counters`. On a **two-socket** node that
combination is fatal to a variorum-enabled build:

```
variorum/config_architecture.c:296 hwloc reports the number of cores (1) mod
the number of sockets (2) is not zero.  Something is amiss.  Exiting.
```

variorum's init does not return an error — it calls `exit()`, terminating the
entire `dftracer_service` process. The pid file has already been written by
that point, so **every liveness check that counts pid files passes** while the
daemon is gone and its trace never grows.

**Symptom → cause → fix**

| | |
|---|---|
| symptom | service trace `service_<host>.pfw.gz` is 0 bytes; pid file present; `start` returned 0 |
| check | `cat <log_dir>/dftracer_server_<host>.err` — the variorum message is the only evidence |
| fix | `export DFTRACER_DISABLE_VARIORUM_POWER=1` in the service environment |
| alternative | give the service ≥1 core per socket (`-c2` on a 2-socket node) so `cores mod sockets == 0` |

Disabling is usually the right call: on a machine where variorum cannot read
power anyway (root-only `/dev/cpu/*/msr`, no `msr_safe`), it is pure downside.
Verify a build's variorum linkage with
`ldd $(which dftracer_service) | grep variorum`.

### 2. `stop` returns before the daemon has flushed — the job step then reaps it

`dftracer_service stop` sends **SIGINT and returns immediately** ("Sent SIGINT
to server (PID …)"). The daemon flushes and gzips its buffer **asynchronously**
after that. If `stop` is the last command in a Flux/Slurm job step, the step
ends, the scheduler tears down the step's cgroup, and the daemon is killed
mid-flush — leaving a 0-byte trace with no error anywhere.

This is why a quick interactive test "works" (there is usually a `sleep` after
`stop`) while the same sequence inside an automated sweep does not.

**Do not use a fixed `sleep`** — it is a race either way. Hold the step open
until the trace is actually non-empty:

```bash
"$DFT_SVC_BIN" stop "$DFT_SVC_DIR"
T="${DFTRACER_LOG_FILE}_$(hostname).pfw.gz"
for _i in $(seq 1 60); do [ -s "$T" ] && break; sleep 1; done
[ -s "$T" ] || echo "WARN: service trace $T still empty after 60s" >&2
```

Relatedly, on Slurm the daemon is a child of its job **step**, so a
fire-and-forget `srun … dftracer_service start` has the daemon reaped the
moment that step exits. The service must run as a **long-lived step** that
starts the daemon, waits for a stop signal (e.g. a flag file), stops it, and
waits for the flush — with the application launched as a separate `--overlap`
step on the same `--nodelist`.

---

## dftracer_merge: does not recurse, and rejects a `.pfw.gz` output name

`dftracer_merge` is the compaction tool (there is no `dftracer_compact` in
current builds). Two sharp edges:

* **`-d <dir>` is not recursive.** It only sees `*.pfw`/`*.pfw.gz` directly in
  that directory. Pointing it at a tree whose traces live one level down fails
  with `No .pfw or .pfw.gz files found in directory: …` even though the files
  plainly exist. Invoke it once per leaf directory.
* **`-o` must end in `.pfw`, not `.pfw.gz`** — otherwise
  `ERROR Output file should have .pfw extension`. Pass `--compress` and the
  tool produces the `.pfw.gz` itself.

```bash
dftracer_merge -d "$leaf/raw/papi_set1" -o "$leaf/compacted/papi_set1.pfw" \
               --compress --force
```

## Installing from the PyPI prerelease sdist with MPI (2026-09-30)

`session_install_dftracer(dftracer_repo="pypi-pre", mpi=True, papi=False,
hip=False, variorum=False, hdf5=False)` downloads ONLY the dftracer sdist
(`--no-binary dftracer --no-deps`; `--no-binary :all:` builds every build dep
from source and stalls), extracts it to `<WS>/tmp/dftracer_sdist`, and pip
installs it with the feature env vars. Verify: `dftracer_config.hpp` has
`DFTRACER_MPI_ENABLE 1`; `libdftracer_core.so` links `libmpi_cray`.

- `-lstdc++fs` must not be added blindly on Tuolumne: Cray clang finds
  gcc-toolset-13's static `libstdc++fs.a`, which needs
  `std::__glibcxx_assert_fail` from a newer libstdc++ than the one linked
  → `dftracer_service` link fails. The tool now adds it only if a
  `std::filesystem` test link fails without it.
- Cray-clang-built dftracer libs carry an RPATH with `/usr/lib64` (old
  libstdc++) first → `CXXABI_1.3.13` / `GLIBCXX_3.4.29` not found at load.
  Patchelf the RPATH of the libs in `dftracer/lib64` to put
  `/opt/cray/pe/gcc-libs` first, then `ldd` must be clean.
- Build order: build the APP first, then dftracer against the app's resolved
  compiler/MPI/venv. `session_install_dftracer` refuses until the app build is
  recorded (`app_built_override=True` for hand-built apps).
- Trace `end` record `cfg.bind` tells you whether interceptors were installed;
  `bind:0` with `build.mpi:1` means initialization happened before INIT (see
  dftracer-annotation-lessons, pre-main constructors).
