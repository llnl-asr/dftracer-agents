---
name: dftracer-install
description: Install and privilege rules for dftracer sessions — never use sudo, always install to userspace paths; autotools pkg-config integration
---

## HDF5 Compatible Versions

dftracer is tested and compatible with exactly these HDF5 releases:

| Series | Specific version to use |
|--------|------------------------|
| 1.8.x  | **1.8.23**             |
| 1.10.x | **1.10.5**             |
| 1.12.x | **1.12.3**             |
| 1.14.x | **1.14.5** (preferred) |

**Default: use 1.14.5.** It has the most complete API support.

Any other major.minor series (e.g. 1.6.x, 1.16.x) is NOT supported by dftracer
and must be replaced.

### Checking the installed version

```bash
h5cc --version 2>/dev/null || h5dump --version 2>/dev/null
# Or: pkg-config --modversion hdf5
```

If the system version is NOT in the table above → build 1.14.5 from source.

### How to install HDF5 1.14.5 from source (userspace, with MPI)

```bash
wget https://github.com/HDFGroup/hdf5/releases/download/hdf5_1.14.5/hdf5-1.14.5.tar.gz
tar xf hdf5-1.14.5.tar.gz && cd hdf5-1.14.5
CC=mpicc ./configure \
  --prefix=<session_ws>/hdf5_1.14 \
  --enable-parallel \
  --enable-shared \
  --enable-build-mode=production \
  --with-zlib=/usr
make -j$(nproc) && make install
export HDF5_DIR=<session_ws>/hdf5_1.14
export LD_LIBRARY_PATH=$HDF5_DIR/lib:$LD_LIBRARY_PATH
```

Then rebuild the application with `-I$HDF5_DIR/include -L$HDF5_DIR/lib -lhdf5`.

HDF5 1.14.x unlocks: `H5Pset_page_buffer_size` with the MPI-IO VFD, the async
VOL connector (`H5Fcreate_async`), and the full collective metadata flush API.

---

## Autotools + dftracer Integration

dftracer is **CMake-only** — no `dftracer.pc` ships by default.

### Call order for autotools projects

```
session_install_dftracer       # cmake mode → libdftracer_core.so in install_ann/lib/
session_generate_dftracer_pc   # generates install_ann/lib/pkgconfig/dftracer.pc
session_patch_build            # injects pkg-config lines into Makefile/Makefile.am
session_build_annotated        # auto-sets PKG_CONFIG_PATH and --disable-dependency-tracking
```

### Never do this in bash

```bash
# WRONG — Make := syntax is not bash
DFTRACER_INC := /path/to/include   # → "DFTRACER_INC: command not found"
```

Use `PKG_CONFIG_PATH` + the generated `.pc` file instead.

### configure failures

- Add `--disable-dependency-tracking` to every `./configure` call.
- Clean stale `.deps` before reconfiguring:
  ```bash
  find <dir> -name ".deps" -type d -exec rm -rf {} + 2>/dev/null
  ```

## Python / pip — Always Use Full Paths

**Never invoke bare `python`, `python3`, or `pip`/`pip3` in shell commands or MCP tool subprocess calls.**
Always use the full path so the active environment is unambiguous:

```bash
# wrong
python my_app.py
pip install dftracer

# correct (session_install_dftracer does this by default; see [[tools-dftracer]] Install sources)
/path/to/venv/bin/python my_app.py
/path/to/venv/bin/pip install --pre "dftracer[modules]" dftracer-modules
/path/to/venv/bin/dftracer-compose --enable mpi,hdf5   # with the app's modules loaded

# in MCP tool Python code — always sys.executable or the venv bin path
import sys
venv_python = venv_dir / "bin" / "python"
_run([sys.executable, "-m", "venv", str(venv_dir)])   # create
_run([str(venv_python), "my_app.py"])                  # run
pip = venv_dir / "bin" / "pip"
_run([str(pip), "install", "dftracer"])                # install
```

The session venv python is always at `<workspace>/venv/bin/python`; the session pip is at `<workspace>/venv/bin/pip`.

---

## Install and Privilege Rules

**Never use `sudo` or install into system paths.** All installs must be userspace only:

- Python packages: use the venv pip — `<workspace>/venv/bin/pip install` (never bare `pip install --user`)
- npm/node: use `npm install --prefix ~/.local` or a project-local `node_modules/`
- CMake/make: always pass `-DCMAKE_INSTALL_PREFIX=<userspace path>` (e.g. `~/.local` or the session's `install/` dir)
- Autotools: always pass `--prefix=<userspace path>` to `./configure`
- Conda: use `conda install` into the active user env, never `sudo conda`
- Binaries: copy to `~/.local/bin/` or the session workspace, not `/usr/local/bin/`

If a build system defaults to `/usr/local` or requires root, override the prefix — do not run with elevated privileges.

---

## RULE 0 — A CONSISTENT ENV IS MANDATORY (install env == run env)

**Read the application's own install/run scripts BEFORE choosing any module.**
Never take modules from `system_detect` defaults, from the README prose, or from
a previous session. Look for, in this order:

```
<app>/scripts/install-<system>.sh      # module set, python, pip extras, patchelf steps
<app>/scripts/<app>-<system>.job       # LD_PRELOAD, runtime env vars, launcher shape
<app>/pyproject.toml                   # extras (e.g. .[rocmwci]) and pinned versions
```

Extract from those files and reuse VERBATIM for BOTH install and run:
python version, module set, `LD_PRELOAD`, `LD_LIBRARY_PATH`, and any `patchelf` fixups.

**Install dftracer inside the SAME script and the SAME venv as the app** — do not
install it separately afterwards. This is especially true for DL workloads, where
torch/mpi4py wheels pin an exact MPI/ROCm/Python ABI.

Symptoms of a divergent env (all seen in real sessions, all traced back to this rule):

| Symptom | Real cause |
| --- | --- |
| `import dftracer.dftracer` silently fails; `NoOpProfiler` used; run exits 0 with an EMPTY trace | wrong python (app wanted 3.11, we used 3.13) → GLIBCXX/RPATH mismatch. `logger.py` swallows the ImportError. |
| `HIP Intercept context start failed`; `torch.cuda.init()` → `Error in dlopen: libcaffe2_nvrtc.so` | wrong/missing ROCm module + missing `libomp` preload. **NOT** "ROCProfiler is broken" — do not disable ROCProfiler to work around it. |
| `double free or corruption (!prev)` at process exit | two MPI runtimes (dftracer linked `libmpi_cray`, app preloads `libmpi_gnu`), or two OpenMP runtimes. |

### Always verify, never assume
```bash
python -c "import dftracer.dftracer"          # native ext MUST import; ImportError is swallowed elsewhere
ldd <site-packages>/dftracer/lib64/libdftracer_core.so | grep -i mpi   # exactly ONE libmpi
python -c "import dftracer.dftracer, torch; torch.cuda.init()"         # tracer + app coexist
```
A zero exit code does **not** mean tracing worked. Check for a non-empty `.pfw`.

## RULE 1 — Pass MPI (and HDF5 if used) explicitly; bind CC/CXX to the loaded MPI

dftracer's `setup.py` reads **environment variables**, not `CMAKE_ARGS`.

```bash
# Bind compilers to the MPI THE APP USES — not just `which mpicc`.
# Under PrgEnv-cray, `which mpicc` is the crayclang wrapper (libmpi_cray.so.12);
# if the app's wheels/LD_PRELOAD use GNU MPICH (libmpi_gnu.so.12), you MUST use
# the GNU wrappers or the process aborts at exit with a double free.
MPI_ROOT=/opt/cray/pe/mpich/<ver>/ofi/gnu/<gccver>
export MPICC=$MPI_ROOT/bin/mpicc
export MPICXX=$MPI_ROOT/bin/mpicxx
export CC=$MPICC CXX=$MPICXX
export DFTRACER_ENABLE_MPI=ON DFTRACER_BUILD_WITH_MPI=ON

# HDF5 only if the app uses it (ScaFFold does not):
export DFTRACER_ENABLE_HDF5=ON HDF5_ROOT=<prefix> HDF5_DIR=<prefix>

# ROCm workloads: put ROCm on CMAKE_PREFIX_PATH or rocprofiler-sdk is silently skipped
export ROCM_PATH=/opt/rocm-<ver>
export CMAKE_PREFIX_PATH="$ROCM_PATH:$ROCM_PATH/lib/cmake:$CMAKE_PREFIX_PATH"
export rocprofiler_sdk_DIR="$ROCM_PATH/lib/cmake/rocprofiler-sdk"
```

## RULE 2 — LD_LIBRARY_PATH gotchas

- Must include `/usr/lib64` (libdl). Cray's linker also runs with
  `--no-allow-shlib-undefined`, so `/usr/lib64` on the path is **necessary but not
  sufficient**: also `export LDFLAGS="-ldl"`. Keep it a single token with no trailing
  space — CMake policy `CMP0004` errors on link items with surrounding whitespace.
- Must include the **CCE runtime libs** at RUN time too if dftracer was built with
  crayclang, else `ImportError: libmodules.so.1: cannot open shared object file`.
- For torch: put `<venv>/lib/pythonX.Y/site-packages/torch/lib` on `LD_LIBRARY_PATH`.
  dftracer's gotcha intercepts `dlopen`, which defeats torch's `$ORIGIN`-relative
  RPATH lookup of its lazily-dlopen'd shims (`libcaffe2_nvrtc.so`).

## RULE 3 — Install ORDER

`dftracer` **before** `dftracer-utils`. `dftracer-utils` installs headers into
`site-packages/dftracer/include/`; a stale `zconf.h` there makes dftracer's own build
fail with `fatal error: 'zlib_name_mangling.h' file not found`.

Do **not** use `--no-build-isolation` (the build needs `setuptools_scm`).
Use `set -o pipefail` — `pip install ... | tee` otherwise returns tee's 0 and hides
build failures.

## RULE 4 — Site pip config can hide the wheel index

A user-level `~/.pip/pip.conf` may override the site `index-url`, making an internal
wheel (e.g. `torch==2.10.0+rocm710` from `.[rocmwci]`) look nonexistent. Force it:

```bash
export PIP_INDEX_URL="https://<site-wheel-index>/simple"
export PIP_TRUSTED_HOST="<site-wheel-host>"
```
Do not "fall back" to a generic extra (`.[rocm]`) — that silently changes the ABI.

## RULE 5 — Initialize once, per process

`cli.py`-style dispatchers often import **every** subcommand module. If two modules each
call `initialize_log()` at import, the singleton is initialized twice and `finalize()`
double-frees. Initialize in exactly one module; elsewhere use `dftracer.get_instance()`.
