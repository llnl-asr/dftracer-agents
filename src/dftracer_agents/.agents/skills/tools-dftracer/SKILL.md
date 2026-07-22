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

Two valid ways to install, both via `pip` (never a manual git clone + hand-driven
cmake build for a session-local install — that's what `session_install_dftracer`
is for, and it already knows the CMake two-pass dependency-bootstrap quirk, see
`dftracer-build-dftracer/pitfalls.md`):

1. **PyPI release**: `pip install dftracer==<version>` (e.g. `2.0.3`). Gets a
   tagged, stable version.
2. **GitHub develop branch via pip's git URL support**:
   `pip install "git+https://github.com/LLNL/dftracer.git@develop"`. Use this
   when you need a fix that hasn't shipped to PyPI yet. This is still a pip
   install, NOT a manual `git clone` + `cmake configure/build/install` — pip
   drives the same build backend, it just resolves the source from the git ref
   instead of a PyPI sdist/wheel.

Prefer `session_install_dftracer` (the MCP tool) for either source — check what
it actually does before assuming; it may already default to develop-branch pip
install with the CMake two-pass bootstrap baked in.

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

## Session-local vs shared venv (MANDATORY)

Never install or repair dftracer inside the shared framework venv
(`/usr/workspace/.../dftracer-agents/.venv`) as a side effect of a session's
build — that venv is the framework's OWN dependency environment, not a place
to fix up for a traced app. Always install a session-local dftracer (via
`session_install_dftracer`) and point the traced app's own build/run at that
session's install, per [[feedback_dftracer_aiml_venv]] (dftracer and the app
share ONE venv — the session's, not the framework's).
