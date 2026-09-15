---
name: tools-dftracer
description: The dftracer PyPI package itself (C core + CMake build) — package layout, dependency graph (dftracer-utils, pybind11, pydftracer), install sources (PyPI release vs GitHub develop via pip), and session-local vs shared-venv install rules. Load this skill before any session_install_dftracer call or before diagnosing a "dftracer works but X sub-feature is missing" report.
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

**MANDATORY rule (corrected 2026-08-04, user-taught, YGM session): never use
the prebuilt distribution when ANY build-time feature flag needs to be
configured — MPI, ROCm/HIP, or HDF5.** The prebuilt wheel is a single fixed
build with whatever flags its own CI happened to use (confirmed: the
Tuolumne prerelease wheel linked NO MPI library at all — `ldd
libdftracer_core.so | grep -i mpi` came back empty). If the traced app needs
dftracer's own MPI-IO interception, HDF5 tracing, or HIP tracing, you MUST
build from source (options 1, 2, or 4 below) with the matching
`DFTRACER_ENABLE_*`/`MPICC`/`MPICXX`/`HDF5_ROOT` env vars — see RULE 1 in
`dftracer-install`. Only reach for the prebuilt distribution (option 3) when
the workload needs nothing beyond plain FUNCTION-mode app-level annotation
tracing with no dftracer-side feature flags at all.

Four valid ways to install (never a manual git clone + hand-driven cmake build
for a session-local install — that's what `session_install_dftracer` is for,
and it already knows the CMake two-pass dependency-bootstrap quirk, see
`dftracer-build-dftracer/pitfalls.md`):

1. **PyPI release**: `pip install dftracer==<version>` (e.g. `2.0.3`). Gets a
   tagged, stable version.
2. **GitHub develop branch via pip's git URL support**:
   `pip install "git+https://github.com/LLNL/dftracer.git@develop"`. Use this
   when you need a fix that hasn't shipped to PyPI yet. This is still a pip
   install, NOT a manual `git clone` + `cmake configure/build/install` — pip
   drives the same build backend, it just resolves the source from the git ref
   instead of a PyPI sdist/wheel.
3. **Prebuilt prerelease distribution (Tuolumne, no compile step, LIMITED — see mandatory rule above)**:
   `session_install_dftracer` does NOT know about this path yet — it must be
   run manually. Skips the entire CC/CXX/LD_LIBRARY_PATH/chid_t/dlopen
   compile-time pitfall surface because nothing gets compiled:
   ```bash
   ml use $HOME/dftracer/distributions/modulefiles
   ml load dftracer-dist
   ml load python/3.11        # must match the modulefile's target Python
   python -m venv venv-311 && source venv-311/bin/activate
   pip install --pre dftracer
   ```
   Installs a prebuilt wheel with `libdftracer_core.so`, headers, AND ready
   CMake config files (`lib64/cmake/dftracer/dftracer-config.cmake`) — no
   `session_generate_dftracer_pc` needed for CMake C++ projects. Caveat: this
   wheel showed no MPI library linked in `ldd` — verify with `ldd
   libdftracer_core.so | grep -i mpi` before relying on it for anything beyond
   FUNCTION-mode app-level annotation. See `dftracer-annotation-lessons`
   LESSONS_LOG.md (2026-08-04, YGM session) for the full trace.

4. **LLNL internal GitLab source (czgitlab, SSH), the canonical org for
   feature-configurable source builds**: same org, one repo per package —
   ```
   ssh://git@czgitlab.llnl.gov:7999/dftracer/dftracer.git
   ssh://git@czgitlab.llnl.gov:7999/dftracer/dftracer-utils.git
   ssh://git@czgitlab.llnl.gov:7999/dftracer/pydftracer.git
   ```
   (dfanalyzer and dfdiagnoser live under the same org too.) Install order:
   `dftracer` BEFORE `dftracer-utils` (RULE 3 in `dftracer-install` — a stale
   `zconf.h` header collision otherwise). `pip install
   "git+ssh://git@czgitlab.llnl.gov:7999/dftracer/dftracer.git"` with the
   `DFTRACER_ENABLE_MPI`/`DFTRACER_ENABLE_HDF5`/`MPICC`/`MPICXX`/`HDF5_ROOT`
   env vars set BEFORE the pip call (RULE 1). SSH access confirmed working
   from Tuolumne compute/login nodes without extra setup (2026-08-04).
   **`czgitlab.llnl.gov` is only reachable from inside the LC (Livermore
   Computing) network** — this works from any LC system (Tuolumne, etc.) but
   will NOT resolve/connect from outside LC. Don't assume this source is
   reachable in a non-LC or external/sandboxed environment; fall back to
   options 1/2 (GitHub/PyPI) there.
   ALWAYS verify the feature actually compiled in — a green pip install does
   not guarantee it: `ldd <prefix>/lib64/libdftracer_core.so | grep -i mpi`
   must show exactly one `libmpi`, not zero.

   **PAPI and Variorum exist ONLY on czgitlab, and asking GitHub for them fails
   SILENTLY.** The public GitHub `develop` branch (seen as package version
   2.0.3) has no `DFTRACER_ENABLE_PAPI_TRACING` / `DFTRACER_ENABLE_VARIORUM`
   option at all. CMake therefore discards both as
   `CMake Warning (unused-cli): Manually-specified variables were not used by
   the project`, the build succeeds, and `session_install_dftracer` still
   returns `status: ok` with
   `features=['mpi', 'papi', 'variorum', 'hwloc']` — the tool reports what it
   *requested*, not what was *compiled*. The installed
   `dftracer_config.hpp` then contains **no `DFTRACER_PAPI_TRACING_ENABLE` and
   no `DFTRACER_VARIORUM_ENABLE` line at all** (not even as `/* #undef ... */`,
   because the `#cmakedefine` is absent from that branch's template), and the
   run produces zero `papi` and zero power events with no error.

   So for any PAPI or Variorum session you MUST pass
   `dftracer_repo="ssh://git@czgitlab.llnl.gov:7999/dftracer/dftracer.git"`
   and then verify **in the installed header**, never in the tool's response:

   ```bash
   CFG=<prefix>/include/dftracer/core/dftracer_config.hpp
   grep -E "PAPI_TRACING_ENABLE|VARIORUM_ENABLE|MPI_ENABLE|HIP_TRACING_ENABLE" $CFG
   # want: #define DFTRACER_PAPI_TRACING_ENABLE 1
   #       #define DFTRACER_VARIORUM_ENABLE 1
   ```

   A correct czgitlab build also prints, in the install log,
   `-- [DFTRACER] found PAPI at ...`, `-- [DFTRACER] detected N PAPI counters
   (M fit in K hardware slots...)` and `-- [DFTRACER] variorum power domains to
   build: ...`. Absence of those three lines means the feature is not in.

   Linkage check, which doubles as the regression test for
   [[bug-dftracer-variorum-linked-into-app-heap-corruption]]:
   `libvariorum` must appear in `ldd <prefix>/bin/dftracer_service` and must
   **NOT** appear in `ldd <prefix>/lib64/libdftracer_core.so` (nor in any
   traced application binary). `libpapi` correctly appears in both.

   Reinstalling over a previous dftracer leaves a stale cmake/brahma tree, so
   remove the session venv first (`session_remove_path(relpath="venv",
   recursive=True)`) rather than reinstalling in place — see
   [[bug-dftracer-stale-brahma-after-pip-uninstall]].

Prefer `session_install_dftracer` (the MCP tool) for sources 1-2 and 4 — check
what it actually does before assuming; it may already default to
develop-branch pip install with the CMake two-pass bootstrap baked in, and may
or may not yet accept an arbitrary git URL (including the czgitlab SSH form) —
if it doesn't, fall back to a manual pip install with the env vars above,
still inside the session's own venv, never the shared framework venv. Source 3
is currently a manual-only path (an MCP tool gap worth closing —
`session_install_dftracer` could grow a `source="prerelease-module"` option).

## CC/CXX/LD_LIBRARY_PATH MUST be set before pip install (this always fails otherwise)

`pip install dftracer` (from PyPI OR from the GitHub develop git URL) compiles
a C/C++ extension under the hood — it is NOT a pure-Python wheel install. This
reliably fails or silently produces a broken/mismatched build if the
environment isn't set BEFORE invoking pip:

```bash
export CC=$(which mpicc)      # or the plain C compiler if the app has no MPI
export CXX=$(which mpic++)    # or mpicxx / the plain C++ compiler
export LD_LIBRARY_PATH="<compiler-runtime-lib-dirs>:$LD_LIBRARY_PATH"
pip install dftracer   # or pip install "git+https://github.com/LLNL/dftracer.git@develop"
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

## Prebuilt prerelease wheel's packaged CMake config is broken for CMake C++ consumers (confirmed 2026-08-04, YGM)

The prerelease distribution (see Install sources, option 3) ships
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
