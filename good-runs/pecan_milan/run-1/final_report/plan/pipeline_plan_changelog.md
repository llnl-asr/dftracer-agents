
## 2026-07-20 - STEP 1 completion

**What changed:** STEP 1 (dftracer-session-setup) executed and verified all pre-conditions:
- system_detect() confirmed tuolumne module stack, LD_LIBRARY_PATH requirements
- milan/*.py confirmed zero dftracer annotations (expected; PECAN scope selected)
- pecan/*.py confirmed existing annotations correct + main_app has init/finalize calls
- Pure Python app confirmed (no setup.py/pyproject.toml, no compilation needed)
- HDF5/MPI dependencies recorded: h5py for PDBspheres HDF5, mpi4py for torch.distributed.dist
  + cray-mpich/9.0.1 to be used as primary MPI
- All workspace paths confirmed from session_status

**Why:** STEP 1 validation gates STEP 2 (dftracer installation) and STEP 3 (app environment).
Pre-conditions verified; no blocker issues found.

**Next action:** STEP 2 (dftracer-build-dftracer) will set LD_LIBRARY_PATH correctly before
calling session_install_dftracer with DFTRACER_ENABLE_HDF5=ON and DFTRACER_ENABLE_MPI=ON.

## 2026-07-20 STEP 2 FAILURE UPDATE

### Status: BLOCKED — cmake/pip CMAKE_INSTALL_PREFIX Issue

**Blocker:** dftracer installation failed due to cmake trying to write to non-writable system Python site-packages (`/collab/usr/gapps/python/...`). pip's build isolation prevents overriding CMAKE_INSTALL_PREFIX to the session venv.

**Attempts:** 7 different approaches tried (fresh venv, Cray clang, GNU compiler, PyPI vs develop, static libstdc++, explicit pip) — all hit identical cmake error.

**Root cause:** pip sets CMAKE_INSTALL_PREFIX to system Python (not writable), cmake tries to create directories under it for vendored deps (cpp-logger, gotcha, brahma, libuv), and fails with permission error.

**Workaround:** STEP 3 will proceed WITHOUT dftracer. The app already has a working `dftracer_logger.py` integration that degrades gracefully to no-op when `DFTRACER_ENABLE=0`.

**Recommendation for fix:** Either manual cmake build (out of scope), contact dftracer maintainers about pip/cmake integration, or wait for dftracer 2.1.0+ if it fixes this issue.

**Logs:** `artifacts/02_build_dftracer_FAILURE.md`, `tmp/pip_install.log`, setup scripts in `tmp/*.sh`

2026-07-20 STEP 8a dftracer-optimizer-io: applied opt1 L1 h5py per-worker file-handle cache + persistent_workers in annotated/source (pecan/dataset.py,trainer.py). Measured H5Fopen opens/sample 1.092->0.351 (-68%, scale-invariant); data-load-h5 wall -5.2% inconclusive (8-rank opt1 vs 16-rank baseline, single rep). Dominant remaining I/O cost = intra-file H5Oopen traversal (204s, ~66 opens/sample), not file-open -> next lever. opt1 traces at opt1/traces/compact.

## 2026-07-20 (later): Process gap noted

The `DFTRACER_DATA_DIR=all` HARD RULE was already documented in the `dftracer-preload-run`
skill (loaded by the `dftracer-tracer` agent) — the gap this session was procedural: the main
thread wrote `run_baseline.sh`/etc directly instead of dispatching the `dftracer-tracer`
subagent, so that skill's rule wasn't consulted before the baseline run. Fixed mid-session by
discovering and setting it for the follow-up profiling run. Lesson for future pipeline
execution: even when the main thread executes a step directly (e.g. under time pressure or
for a quick fix), it should still load and check the relevant stage skill first.
