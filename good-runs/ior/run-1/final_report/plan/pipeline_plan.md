# DFTracer Pipeline Plan — ior/20260710_172024

## Overview

- **App:** IOR 4.0.0 (`https://github.com/hpc/ior.git`, tag `4.0.0`), C, autotools build.
- **Session workspace (WS):** `$PROJECT_ROOT/workspaces/ior/20260710_172024`
  - `source/` — pristine clone
  - `baseline/source`, `baseline/traces/raw`, `baseline/traces/compact`, `baseline/scripts` — baseline run paths (see `session_get_run_paths(run_id, "baseline")`)
  - `annotated/` — dftracer-instrumented copy
  - `artifacts/` — ALL logs go here, named `<step>_<what>.log`
  - `performance/` — profiling (profile_bind already done)
  - `dataset/` — scratch for large run outputs if needed (prefer Lustre for actual I/O data per feedback_lustre_io; workspace stays for traces/scripts)
- **System:** Tuolumne, AMD MI300A, Cray PE, flux launcher. `system-detect` already run for this session — SKIP step 0, but confirm compilers/MPI/module state are still loaded in the shell wrapper each build/run step uses (per `system-tuolumne` skill / feedback_tuolumne_modules).
- **Allocation (MANDATORY, do not re-derive):** Use the EXISTING flux allocation `<flux-jobid>` (8 nodes, e.g. tuolumne<node>...) via `flux proxy <flux-jobid> bash <wrapper>.sh ...`. Do **NOT** `flux batch`/`flux alloc` a new job. Every flux command in every step below MUST go through `flux proxy <flux-jobid>`.
  - Per `feedback_flux_proxy_wrapper` memory: ALWAYS write a bash wrapper script under `<WS>/scripts/` (module loads, env vars, the actual command) and invoke it as `flux proxy <flux-jobid> bash <WS>/scripts/<name>.sh`. Never inline `bash -c "module load ...; ..."` through the proxy.
  - Before using the allocation, check remaining time: `flux jobs -no "{id} {state} {t_remaining}" <flux-jobid>`. If it's about to expire, surface that to the user immediately — do not silently spawn a replacement allocation.
  - Never block the foreground on `flux proxy`; long-running builds/runs must use `run_in_background: true` and be polled.
- **Run sizing (MANDATORY):** Every measured run (baseline AND every optimization iteration) must be sized for ~10 minutes wall-clock, not a few seconds. Pick proc count that fits inside 8 nodes (e.g. 8 nodes x 64 ranks/node = 512, or a smaller ranks-per-node count if memory/network constrained — confirm with a quick `flux resource list` under the proxy). Pick IOR `-b` (block size), `-t` (transfer size), `-s` (segment count) to hit ~10 min total (write+read) at the measured/expected bandwidth. Concretely: start from the workload-ior skill's known VAST numbers (352-2163 MiB/s per rank scenario at 192 ranks) and scale block size/segments so `total_bytes / expected_BW ≈ 600s`. The tracer step must actually calibrate (short probe run) rather than guess blindly, then fix the same data volume across baseline and every optimization iteration (equal-work rule).
- **Filesystem note:** `stat -f -c %T` the output directory FIRST. Prior IOR session found ROMIO hints behave OPPOSITELY on Lustre vs VAST (see workload-ior skill "Failed Configurations" and the ROMIO table). Do not assume VAST — Tuolumne primary storage is `/p/lustre5`; confirm which mount the run directory actually resolves to before applying any L2/L3 hint.
- **Known prior results (from `workload-ior` skill and `memory/project_ior_optimization`):** On VAST at 192 ranks: `romio_cb_write=enable` is a clear win (+58% write BW, -33% total time); `romio_cb_read=enable` REGRESSES read BW -70%; `romio_ds_write=disable` is FATAL (-73% write BW). On Lustre, cb_read/cb_write usually HELP (opposite of VAST) and striping (`lfs setstripe`) is the key L3 lever. The optimizer step MUST read this skill and check filesystem type before proposing/re-testing any of these hints — do not re-derive from scratch, but DO re-verify on whichever filesystem this run actually lands on since the story flips.
- **Model levels:** per `models.yaml` — build/setup agents level_1, annotator level_2, build-smoke level_3, analyzer/diagnoser level_3, optimizer level_4.

## STEP 0: dftracer-system-detect (COMPLETE — informational only)

System detection was already run for this session prior to planning (Tuolumne, AMD MI300A, Cray PE, flux launcher, allocation `<flux-jobid>` with 8 nodes already granted). No action needed. If any later step finds modules not loaded in its own shell, it must re-run the module load sequence itself (module state does not persist across separate flux proxy invocations) — see `system-tuolumne` skill.

## STEP 1: dftracer-build-dftracer

**Inputs:**
- `run_id=ior/20260710_172024`
- Tools to try first: `mcp__dftracer__session_install_dftracer` / `session_install_dftracer_utils`. On Tuolumne these are KNOWN to fail two ways (per `workload-ior` skill): auto-enables HIP tracing (fails, no rocprofiler-sdk), and resolves HDF5 to old `/usr` 1.10.5. If the MCP tool fails this way, fall back to the documented manual recipe in `workload-ior` skill section "dftracer MPI+HDF5 install on Tuolumne (Cray) — full working recipe":
  1. Patch Cray HDF5 `chid_t` typo into a private include tree copied from `/opt/cray/pe/hdf5-parallel/1.14.3.7/crayclang/20.0`.
  2. `module unload cray-hdf5-parallel` BEFORE building (else cc-wrapper re-injects unpatched include path).
  3. `export DFTRACER_ENABLE_MPI=ON DFTRACER_ENABLE_HDF5=ON DFTRACER_ENABLE_FTRACING=ON DFTRACER_ENABLE_HIP_TRACING=OFF DFTRACER_DISABLE_HWLOC=ON`
  4. `export MPI_C_COMPILER=$(which mpicc) MPI_CXX_COMPILER=$(which mpicxx) HDF5_ROOT=<patched> HDF5_DIR=<patched>`
  5. `pip install --no-cache-dir --force-reinstall --no-deps -v "git+https://github.com/llnl/dftracer.git@develop"` inside the session venv.
  6. Verify `DFTRACER_MPI_ENABLE`, `DFTRACER_HDF5_ENABLE`, `DFTRACER_FTRACING_ENABLE` are all `1` in `dftracer_config.hpp`.
- Run this INSIDE the flux allocation wrapper if it needs compute-node compilers (`flux proxy <flux-jobid> bash <WS>/scripts/install_dftracer.sh`), otherwise on the login/build node consistent with how the app will be built (same modules as STEP 2/4 — env consistency is mandatory).
- Write install log to `<WS>/artifacts/01_build_dftracer.log`.

**Expected artifact:** dftracer installed into the session venv/prefix with MPI+HDF5+FTRACING confirmed ON; installed path recorded back into `pipeline_plan.md` Overview for STEP 4/5 to reuse (update `HDF5_ROOT`, dftracer include/lib paths).

## STEP 2: dftracer-build-app

**Inputs:**
- `run_id=ior/20260710_172024`, source at `<WS>/source`.
- Tools first: `session_configure` / `session_build_install`. IOR 4.0.0 needs manual autoreconf per `workload-ior` skill:
  ```bash
  cd <WS>/source && touch NEWS AUTHORS && autoreconf -fi -I config
  CFLAGS="-g -O2 -Wno-incompatible-function-pointer-types -fcommon" \
  LDFLAGS="-fuse-ld=bfd" \
  ./configure --with-hdf5 --prefix=<WS>/install ...
  make clean && make -j8 install
  ```
- Build inside a wrapper script through `flux proxy <flux-jobid> bash <WS>/scripts/build_ior.sh` if the build must run on a compute node (Cray cc-wrapper/module state); otherwise on the node where session was created, matching the same modules as STEP 1/4.
- For HDF5 backend, ensure stale `.deps/`/`config.status`/`autom4te.cache` are removed first (`make distclean`) so `--with-hdf5` is actually picked up; verify `grep USE_HDF5_AIORI config.h` shows 1.
- Log to `<WS>/artifacts/02_build_app.log`.

**Expected artifact:** working `<WS>/install/bin/ior` (or `<WS>/source/src/ior`) binary; confirm with a 1-process smoke run `./src/ior -a POSIX -b 1m -t 1m -s 1 -F -C -o /tmp/ior_smoke_test`.

## STEP 3: dftracer-annotator

**Inputs:**
- `run_id=ior/20260710_172024`. Language: C. Source to annotate: copy of `<WS>/source` into `<WS>/annotated`.
- Tool-first: `graph_ensure(run_id=...)` + `graph_query` to locate `main()`, `ior_main()`, and backend `_Create`/`_Open`/`_Xfer`/`_Close` functions in `src/aiori-POSIX.c`, `src/aiori-MPIIO.c`, `src/aiori-HDF5.c`, `src/ior.c` — do NOT grep/Read the whole tree.
- Known pitfalls from `workload-ior` skill (apply these BEFORE lint-checking):
  1. `DFTRACER_C_FINI()` must appear AFTER `ior_main()` returns, not before — placing it earlier untraces all backend spans.
  2. Braceless single-line `if (dryRun) return NULL;` patterns: adding braces is REQUIRED before inserting `DFTRACER_C_FUNCTION_END()`, else the END call steals the if-body and the return becomes unconditional. Pre-check: `grep -n "if.*return\|if.*continue\|if.*break" <file.c> | grep -v "{" | grep -v "//"`.
  3. `HDF5_Create` (and similar) appears twice — forward declaration (ends `;`) vs definition (has body). Only annotate the definition: `grep -n "HDF5_Create" file.c | grep -v ";$"`.
  4. `DFTRACER_C_INIT` args MUST be `NULL, NULL, NULL` (not `-1`) — `process_id` is `int *`, passing `-1` is an incompatible integer-to-pointer conversion.
- Scope: annotate `main()` in `src/ior.c`, `ior_main()`, and the POSIX/MPIIO/HDF5 backend functions actually exercised by the smoke test command (confirm via a quick `-a POSIX,MPIIO,HDF5` dry run if ambiguous). Exclude `contrib/` (known broken under cce, unrelated to annotation).
- Write annotated files via `session_write_file(subfolder="annotated")`.
- Self-verify: grep the annotated files for balanced `DFTRACER_C_FUNCTION_START`/`END` pairs and for the FINI-after-ior_main placement before declaring success (memory: `bug_annotator_fabricated_report` — never claim success without grep-verifying).

**Expected artifact:** `<WS>/annotated/src/*.c` instrumented; list of annotated functions/files reported back; any deviation from the pitfalls above called out explicitly.

## STEP 4: dftracer-build-smoke

**Inputs:**
- `run_id=ior/20260710_172024`, subfolder `<WS>/annotated`.
- Known working link recipe from `workload-ior` skill (session_build_annotated does NOT pass custom CFLAGS/LDFLAGS for autotools — build manually):
  ```bash
  rm -rf <WS>/build_ann && mkdir -p <WS>/build_ann && cd <WS>/build_ann
  DFT=<dftracer install>/lib/python3.13/site-packages/dftracer   # from STEP 1's actual install path
  CFLAGS="-g -O2 -Wno-incompatible-function-pointer-types -fcommon -I$DFT/include"
  LDFLAGS="-fuse-ld=bfd -L$DFT/lib64 -Wl,-rpath,$DFT/lib64 -Wl,--allow-shlib-undefined -Wl,--no-as-needed"
  LIBS="-ldftracer_core -lstdc++"
  export LD_LIBRARY_PATH="$DFT/lib64:/usr/tce/packages/python/python-3.13.2/lib:$LD_LIBRARY_PATH"
  <WS>/annotated/configure --with-hdf5 --prefix=<WS>/install_ann ...
  make -C src -j8 install   # build src/ only; skip contrib/
  ```
- Run this build via the wrapper-script + `flux proxy <flux-jobid>` pattern if it must execute on a compute node consistent with the app/dftracer modules.
- Smoke test (1-2 ranks, tiny size, inside the SAME allocation): `DFTRACER_ENABLE=1 DFTRACER_LOG_FILE=<WS>/traces/smoke DFTRACER_DATA_DIR=all <install_ann>/bin/ior -a POSIX -b 1m -t 1m -s 1 -F -C -o <WS>/tmp/ior_smoke_test`. Verify a non-empty `.pfw`/`.pfw.gz` is produced AND `python -c "import dftracer.dftracer"` succeeds in the same env.
- Log to `<WS>/artifacts/04_build_smoke.log`.

**Expected artifact:** `<WS>/install_ann/bin/ior` built and confirmed producing valid non-empty trace output for at least POSIX (and MPIIO/HDF5 if `--with-hdf5` succeeded).

## STEP 5: dftracer-tracer

**Inputs:**
- `run_id=ior/20260710_172024`, `run_name=baseline`. Canonical baseline paths already resolved: `traces_raw=<WS>/baseline/traces/raw`, `traces_compact=<WS>/baseline/traces/compact`, `scripts_dir=<WS>/baseline/scripts`, `dftracer_log_prefix=<WS>/baseline/traces/raw/baseline`.
- **Allocation:** `flux proxy <flux-jobid> bash <WS>/scripts/run_baseline.sh` — write the wrapper script under `<WS>/baseline/scripts/` (or `<WS>/scripts/`) with module loads + env + the actual `flux run` invocation; never inline through the proxy. Check `flux jobs -no "{id} {state} {t_remaining}" <flux-jobid>` first.
- **Proc count:** fits within 8 nodes — confirm actual cores/GPUs per node via `flux resource list` under the proxy, then choose ranks (e.g. 8 nodes x 64 ranks = 512, or fewer if memory-bound). Record the chosen count in this section once known.
- **Run sizing (10 min target, MANDATORY):** Do a short CALIBRATION run first (small `-b`/`-t`/`-s`) to measure achieved MiB/s for this rank count and filesystem (do not assume VAST numbers from the old skill apply — Tuolumne default storage is `/p/lustre5`; run `stat -f -c %T <output_dir>` to confirm). Then compute `-b` (block size per process) and `-s` (segments) so `total_bytes ≈ measured_BW * 600s` for combined write+read, using `-a POSIX` and/or `-a MPIIO`/`-a HDF5` (whichever backends survived annotation/build). Fix this exact data volume (`-b`, `-t`, `-s`, proc count) as the "baseline config" — it MUST be reused unchanged (equal-work rule) for every optimization iteration in STEP 8 unless the knob under test IS one of these, in which case say so explicitly.
- Env: `DFTRACER_ENABLE=1 DFTRACER_LOG_FILE=<WS>/baseline/traces/raw/baseline DFTRACER_DATA_DIR=all`. No ROMIO hints yet — this is the unmodified baseline.
- After run: copy any `.pfw.gz` landing in a `<run_id>` subdirectory up into `traces/raw/` directly if `session_split_traces` only looks flat (known IOR pitfall — run_id has a `/` in it).
- Take at least one replicate (rerun) of this exact baseline config to establish a noise band before STEP 8 compares against it.
- Log to `<WS>/artifacts/05_tracer_baseline.log`.

**Expected artifact:** `<WS>/baseline/traces/raw/*.pfw.gz` (>=1 replicate), the fixed IOR command line (ranks, `-b/-t/-s`, backend) recorded back into this plan file for STEP 8 to reuse, measured wall-clock time (target ~10 min), measured write/read BW, and the filesystem type (`lustre` vs `vast`) of the output directory.

## STEP 6: dftracer-analyzer then dftracer-diagnoser

**Inputs:**
- `run_id=ior/20260710_172024`, `run_name=baseline`, traces at `<WS>/baseline/traces/raw`.
- `mcp__dftracer__session_split_traces` then `session_analyze_traces`. Known gotcha (`workload-ior` skill): with many per-rank `.pfw.gz` files, `cluster_n_workers>1` RACES on the shared `.dftindex` and silently ingests a partial subset. Use `cluster_type=local cluster_n_workers=1` (per `feedback_analysis_parallel_workers`: actually the memory says use `cluster_n_workers=32` generically for speed — but workload-ior explicitly flags a RACE bug for IOR's many-small-file case; prefer `cluster_n_workers=1` for IOR specifically and note the discrepancy if it's slow). Wipe stale `.dftindex`/`checkpoint/` before re-analyzing.
- `mcp__dftracer__diagnose` is KNOWN BROKEN currently (`'Diagnoser' object has no attribute 'diagnose_checkpoint'`, API drift). Fall back to reading `checkpoint/_flat_view_*.parquet` + `_raw_stats_*.json` directly (pandas/pyarrow) to extract: POSIX op counts, seek_ops_slope, data_ops_slope, per-op time breakdown, read vs write bandwidth, any MPI collective time (Barrier/Reduce/Bcast) if `-a MPIIO`/`HDF5` backend used.
- Preset: POSIX view at minimum; add MPIIO/HDF5 view if that backend was built and used.
- Log to `<WS>/artifacts/06_analyze.log`.

**Expected artifact:** ranked bottleneck list (e.g. seek_ops_slope, data_ops_slope, POSIX call count, read/write BW) with concrete numbers, to hand to the optimizer; note filesystem type again (lustre/vast) since it gates which ROMIO hints are safe to try.

## STEP 7: dftracer-optimizer

**Inputs:**
- `run_id=ior/20260710_172024`. Baseline config (ranks, `-b/-t/-s`, backend) and baseline metrics from STEP 5/6.
- **MANDATORY — check prior knowledge FIRST, before proposing anything:** load `workload-ior` skill's "ROMIO Optimization" and "Failed Configurations" sections, `dftracer-io-optimization` (a.k.a. dftracer-optimization-kb) skill, and `memory/project_ior_optimization.md`. These already document, for VAST at 192 ranks: `romio_cb_write=enable` = win (+58% write BW), `romio_cb_read=enable` = regression (-70% read BW), `romio_ds_write=disable` = FATAL (-73% write BW). Do NOT re-derive these from a blank slate — but DO re-verify against whichever filesystem THIS run actually lands on (STEP 5 recorded lustre vs vast), because the skill explicitly warns the story is OPPOSITE on Lustre (cb_read/cb_write usually help there; striping is the key lever instead).
- **Comprehensive, multi-level loop (MANDATORY — do not stop at L1):**
  - **L1 (app-level):** IOR flag tuning within the fixed data-volume envelope from baseline (e.g. `-C` reorder, `-Y` cross-check, transfer size adjustments that don't change total bytes moved unless explicitly flagged as a work-changing test).
  - **L2 (software/MPI-IO/ROMIO/HDF5):** `MPICH_MPIIO_HINTS` (`romio_cb_write`, `romio_cb_read`, `romio_ds_write`, `romio_ds_read`, `cb_nodes`), HDF5 collective metadata / alignment settings if HDF5 backend built.
  - **L3 (filesystem/storage):** if Lustre — `lfs setstripe -c <count> -S <size>` on the output directory before each run; if VAST — confirm no striping knobs apply, focus on L2 hints only.
  - For EACH candidate config: run it at the SAME fixed proc count / `-b/-t/-s` / duration target (~10 min, reuse STEP 5's calibrated sizing) via the SAME `flux proxy <flux-jobid> bash <wrapper>.sh` pattern, trace it, analyze it (reuse STEP 6's method), and compare numerically against the baseline replicate(s) — not against a single baseline sample (respect the noise band).
  - Record EVERY result — win or regression — back into `workload-ior` skill's "Failed Configurations" section format (only after user confirms per the confirmation gate) so future sessions don't repeat a known-bad config.
  - Termination: stop when no further L1/L2/L3 candidate beats the best-so-far by more than the measured noise band, or after a reasonable iteration cap (e.g. 6-8 configs across the three levels) — report whichever came first.
- Log every run to `<WS>/artifacts/07_optimize_<config-name>.log`.

**Expected artifact:** a comparison table (config -> write/read BW, total time, POSIX op counts) spanning at least one L1, one L2, and one L3 (or documented-not-applicable-on-this-filesystem) candidate, each compared against the baseline noise band; the single best overall config and why.

## STEP 8: dftracer-report / final summary

**Inputs:** `run_id=ior/20260710_172024`, all artifacts from STEPs 1-7.
- Produce the final performance report: call `mcp__dftracer__profile_report(run_id=ior/20260710_172024)` (writes `<WS>/performance/performance_report.md`).
- Summarize: baseline vs best-optimized numbers, filesystem type, which ROMIO/striping hints won or lost and why, and the fixed proc-count/data-volume envelope used throughout.
- This step does NOT itself run further I/O — it only collects and reports what STEPs 1-7 already measured.

## STEP 9: dftracer-privacy-guard

**Inputs:** `run_id=ior/20260710_172024`.
- Run `privacy_scan()` across everything written this session: `pipeline_plan.md`, any skill/memory proposals from STEP 7, agent logs in `<WS>/artifacts/`.
- Must report `clean` before the session is considered done. If it finds usernames, absolute `/g/g92/<user>` or `/usr/WS2/<user>` paths, the flux jobid `<flux-jobid>`, or hostnames like `tuolumne<node>` in anything destined for a git-tracked skill/memory file, redact to `$USER`/`$PROJECT_ROOT`/`<flux-jobid>`/`<system><node>` before persisting. Note: the flux jobid and node names are fine to keep in the LIVE session workspace (`workspaces/...` is gitignored) but must NOT appear in any skill/MEMORY.md edit.

## DISPATCH ORDER

dftracer-build-dftracer, dftracer-build-app, dftracer-annotator, dftracer-build-smoke, dftracer-tracer, dftracer-analyzer, dftracer-diagnoser, dftracer-optimizer, dftracer-privacy-guard
