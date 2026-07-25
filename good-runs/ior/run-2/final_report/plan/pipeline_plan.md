# DFTracer Pipeline Plan — ior/20260724_175545

## Overview

- App: IOR 4.0.0 (https://github.com/hpc/ior, ref IOR-4.0.0), Autotools build, C/C++.
- System: tuolumne (AMD MI300A APU cluster, Cray PE). Modules: craype-x86-trento,
  libfabric/match_SHS, craype-network-ofi, perftools-base/25.09.0, craype/2.7.35,
  PrgEnv-cray/8.7.0, flux_wrappers/0.1, xpmem/2.6.5, cce/20.0.0, cray-libsci/25.09.0,
  cray-mpich/9.0.1, python/3.13.2. MPI launcher: `flux run -n <N>`.
  `LD_LIBRARY_PATH` MUST include `/opt/cray/pe/cce/20.0.0/cce/x86_64/lib`,
  `.../lib/default64`, and `/usr/lib64` (for libdl) BEFORE calling
  session_install_dftracer / session_build_annotated — set it in the wrapper
  script, not via an interactive Bash export (separate process).
- HDF5: built from source into `<WS>/hdf5-1.14.5` / `<WS>/install_hdf5` (NOT
  Cray/system HDF5 1.10.5) — this is the key delta vs the prior session
  (ior/20260710_172024), which used an older HDF5 without H5Pset_all_coll_metadata_ops
  / VOL support. Use `HDF5_ROOT=<WS>/install_hdf5` for both the dftracer install
  and the app build.
- MPI: cray-mpich 9.0.1, mpicc/mpicxx at
  `/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/{mpicc,mpicxx}`. Bind
  `CC=$(which mpicc) CXX=$(which mpic++)` after modules load, use for BOTH the
  app build and the dftracer install (policy: install env == run env).
- run_id: `ior/20260724_175545`
- Workspace: `$PROJECT_ROOT/workspaces/ior/20260724_175545`
- Canonical subdirs: `source/`, `baseline/` (has `source/`, `patches/`,
  `traces/raw`, `traces/compact`, `scripts/`), `annotated/`, `build/`,
  `install/`, `hdf5-1.14.5/`, `install_hdf5/`, `artifacts/`, `scripts/`,
  `tmp/`, `dataset/` (symlink -> `$LUSTRE_ROOT/workspaces/ior`,
  PFS — all IOR test-file output MUST land here, never inside the
  workspace itself, per policy rule 11).
- Session already: cloned, detected, configured (`step: configured`).
  This plan covers: annotate -> build-smoke -> baseline trace -> analyze/diagnose
  -> optimize (4 dimensions) -> report -> privacy-guard.

### Baseline config carried forward from ior/20260710_172024 (validated, see
memory `project-ior-hdf5-optimization-dftracer-pipeline`)
- IOR flags: `-a HDF5 -b 16m -t 4k -s 32 -C -F` (file-per-process, contiguous).
- Scale: 512 ranks / 8 nodes was stable. Prior write throughput 18-22 GiB/s,
  read 11.5-12.5 GiB/s.
- Prior exhaustive ROMIO/striping sweep found NO lever beat the plain
  4KB-transfer baseline for this file-per-process/contiguous access pattern —
  a validated NEGATIVE result. Do not re-litigate it wholesale; instead
  SPECIFICALLY re-test whether newer HDF5 1.14.5 collective metadata ops
  (`H5Pset_all_coll_metadata_ops`, VOL connectors — unavailable in the prior
  HDF5 version) change that conclusion. This is a targeted re-verification,
  not a blind repeat of the full sweep.
- Per policy rule 14, the optimizer MUST still walk all 4 dimensions
  (io/compute/communication/memory) even though prior evidence points to I/O
  being near a hardware floor — compute/communication/memory may have
  independent, unexplored levers (e.g. MPI collective tuning for metadata ops,
  memory pinning, thread affinity).

### dftracer_service node-counter daemon (policy rule 12)
Every stage that launches the app (smoke test, baseline trace, each
optimization variant run) MUST bracket the `flux run` launch with
`session_service_start` immediately before and `session_service_stop`
immediately after — one instance per node, pinned to a single core
(`-n<nodes> -c1`, never `--tasks-per-node`). Verify required env vars are set
or the daemon silently no-ops (see memory
`feedback-dftracer-service-node-counters`).

### Logging
All build/run/analysis logs go to `<WS>/artifacts/<step>_<what>.log`, never
left only in terminal output and never under `<WS>/tmp/` (scratch/wrapper
scripts only).

---

## STEP 1: dftracer-annotate-c

**Inputs**
- Source tree: `<WS>/source/` (IOR C/C++ source — `ior.c`, `utilities.c`,
  `aiori-POSIX.c`, `aiori-HDF5.c`, `aiori-MPIIO.c`, and other `aiori-*.c`
  backends). Copy the pieces you annotate into `<WS>/annotated/` (session
  convention: annotated/ mirrors source/ with DFTRACER macros added).
- Language: C (some C++ glue) — use `dftracer-annotate-c` directly (already
  scoped narrowly; no need to route through the generic `dftracer-annotator`
  dispatcher for this single-language app).
- Smoke-test command to scope which files matter (functions actually on the
  hot path for `-a HDF5 -b 16m -t 4k -s 32 -C -F`):
  `flux run -n 4 <WS>/build_ann/src/ior -a HDF5 -b 16m -t 4k -s 32 -C -F -o <WS>/dataset/smoke/testFile`
- Macros: `DFTRACER_C_FUNCTION_START` / `DFTRACER_C_FUNCTION_END` around I/O
  entry points (open/write/read/close/fsync) in `aiori-HDF5.c`,
  `aiori-POSIX.c`, `aiori-MPIIO.c`, plus top-level benchmark loop functions in
  `ior.c` / `utilities.c`. Exclude tight inner loops that run per-byte/per-op
  (e.g. per-transfer-size loops) — annotate at the per-call granularity dftracer
  expects, not per-transfer.
- Since HDF5 is now built from source (1.14.5) with newer VOL/collective
  metadata support, also confirm annotation coverage includes any
  `H5Pset_all_coll_metadata_ops` / collective-metadata call sites in
  `aiori-HDF5.c` if present — this is the specific new lever this session is
  re-testing.

**Expected artifacts**
- Annotated files written under `<WS>/annotated/` mirroring `<WS>/source/`
  layout.
- Annotation coverage summary (file count annotated, functions annotated) —
  report this to the plan/changelog.
- Validation: use `dftracer-validate-c` (or the annotator's own lint step) to
  confirm every `_START` has a matching `_END` before handing off — see memory
  `bug_annotator_fabricated_report`: always grep-verify write claims, don't
  trust a summary alone.

**Decision point for human**: if file count to annotate exceeds ~10, list them
and confirm scope before annotating (IOR is small; likely well under this, but
state the count).

---

## STEP 2: dftracer-build-smoke

**Inputs**
- Subfolder: `<WS>/annotated/`
- Build: autotools — `./configure --prefix=<WS>/install_ann
  --with-hdf5=<WS>/install_hdf5 CC=$(which mpicc) CXX=$(which mpic++)
  LD_LIBRARY_PATH=/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:$LD_LIBRARY_PATH`
  then `make -j`. Link against dftracer (`libdftracer_core.so`) — FUNCTION mode
  only, never LD_PRELOAD (policy rule 13).
- dftracer must already be installed into a shared prefix in this same env
  (same modules, same CC/CXX, same HDF5) — if not yet installed, this step
  should invoke `dftracer-build-dftracer` first (session status shows no
  install/ dftracer artifacts recorded yet — verify with
  `session_status`/`session_validate_structure` before assuming it's done).
- `DFTRACER_INIT` mode: FUNCTION (source-level annotation compiled in).
- Smoke command (small scale, sanity only):
  `flux run -n 4 <WS>/build_ann/src/ior -a HDF5 -b 16m -t 4k -s 4 -C -F -o <WS>/dataset/smoke/testFile`
  with `DFTRACER_LOG_FILE=<WS>/baseline/traces/raw/smoke` and
  `DFTRACER_DATA_DIR=all` (or app-specific data dir env per dftracer skill).
- Bracket the launch with `session_service_start` / `session_service_stop`
  (policy rule 12), pinned `-n<nodes> -c1`.

**Expected artifacts**
- Successful build log at `<WS>/artifacts/02_build_smoke.log`.
- Non-empty `.pfw` trace file(s) under `<WS>/baseline/traces/raw/` confirming
  FUNCTION-mode tracing actually fired (verify with `python -c "import
  dftracer.dftracer"` plus a non-empty trace file check — a zero exit code
  alone does NOT confirm tracing worked, per policy note on HPC Python env).
- `dftracer_service` counter file(s) alongside (`service_<hostname>.*`).

---

## STEP 3: dftracer-tracer

**Build confirmed (STEP 2, done)**
- Annotated binary is at `<WS>/install_ann/bin/ior` (NOT `<WS>/build_ann/src/ior`
  -- the app is Autotools, `session_build_annotated`'s cmake path doesn't apply;
  a custom build was used, captured reproducibly in
  `<WS>/annotated/scripts/build.sh`). Re-run that script for any rebuild --
  it already applies the AC_PREREQ 2.71->2.69 fix, `--with-hdf5=yes` +
  CPPFLAGS/LDFLAGS for HDF5, and `LIBS=-ldftracer_core`.
- Env for any launch: `source <WS>/scripts/env.sh` (modules) THEN append
  (never overwrite) `LD_LIBRARY_PATH=<WS>/install_hdf5/lib:<WS>/install_dftracer/lib/python3.13/site-packages/dftracer/lib64:$LD_LIBRARY_PATH`
  -- overwriting drops cray-mpich's `libmpi_cray.so.12`/`libpmi.so.0` that
  the module load set, and the binary fails to start.
- Smoke-verified single-process: HDF5 and MPIIO backends both produced
  non-empty `.pfw.gz` traces with categories
  {MPI, POSIX, HDF5, MPIIO, STDIO, C_APP, dftracer}.

**Inputs**
- Ask the user (or use the standing preference) which allocation to use:
  existing `flux proxy <JOBID>` wrapper vs new `flux batch -N 8 -q pdebug`.
  Check remaining time on any named JOBID before starting.
- Run command (baseline, full scale, matches prior validated config):
  `flux run -N 8 -n 512 <WS>/install_ann/bin/ior -a HDF5 -b 16m -t 4k -s 32 -C -F -o <WS>/dataset/baseline/testFile`
- Env: `DFTRACER_LOG_FILE=<WS>/baseline/traces/raw/baseline`,
  `DFTRACER_DATA_DIR=all` (or whatever the dftracer skill specifies for
  FUNCTION mode + HDF5 backend), `HDF5_ROOT=<WS>/install_hdf5` on
  `LD_LIBRARY_PATH` (appended, per note above).
- Target run length: this is a throughput benchmark, not a training loop —
  no fixed epoch/time-budget calibration needed, but DO take at least one
  replicate of the baseline run to establish a noise band before crediting any
  optimization delta (policy: report deltas against measured noise, not bare
  percentages).
- Bracket with `session_service_start` / `session_service_stop` (rule 12),
  one instance per node, `-N 8 -n8 -c1` or per-hostname invocation as the
  service tool expects.
- All IOR test files write to `<WS>/dataset/baseline/` (PFS, symlinked) —
  never inside the workspace.
- Run `session_split_traces` after the run completes.

**Expected artifacts**
- `run_name`: `baseline`
- Split trace directory: `<WS>/baseline/traces/compact/`
- Baseline throughput numbers (write/read GiB/s) logged to
  `<WS>/artifacts/03_baseline_run.log`, expect roughly write 18-22 GiB/s /
  read 11.5-12.5 GiB/s if the HDF5 1.14.5 upgrade doesn't change behavior —
  flag clearly if it does (that is the interesting result this session is
  after).
- At least 2 replicates of the baseline (state noise band).

---

## STEP 4: dftracer-analyzer

**Inputs**
- Trace dir: `<WS>/baseline/traces/compact/`
- Preset: POSIX/HDF5 combined preset (IOR HDF5 backend rides on POSIX I/O
  under the hood) — use whichever preset the analyzer skill designates for
  mixed POSIX+HDF5 (check `list_presets` if unsure; do not default to DLIO
  preset, this is not a DL workload).
- Views: I/O size distribution, per-rank throughput, HDF5 metadata op timing
  (specifically look for `H5Pset_all_coll_metadata_ops` / collective metadata
  call costs — this is the metric that answers the "does newer HDF5 change
  the conclusion" question), POSIX read/write op counts, MPI collective time.

**Expected artifacts**
- Analysis summary JSON/report under `<WS>/artifacts/04_analyze_baseline.log`
  (or wherever `session_analyze_traces` writes it).
- Hand off directly to dftracer-diagnoser (same step, chained) for bottleneck
  classification: is throughput I/O-bound (hardware floor, confirming prior
  finding), or is there daylight from metadata-ops overhead / MPI collective
  stalls / memory copy overhead that the 4-dimension optimizer can attack?

**Diagnoser output expected**: ranked bottleneck list across all 4 dimensions
(io, compute, communication, memory) even if I/O dominates — feed this list
directly into STEP 5.

---

## STEP 5: dftracer-optimizer

Per policy rule 14, dispatch ALL FOUR component subagents against the
diagnosed bottleneck list every time — not just the dimension flagged as
dominant.

**Inputs (all four subagents receive the same baseline facts)**
- Baseline: `-a HDF5 -b 16m -t 4k -s 32 -C -F`, 512 ranks / 8 nodes,
  HDF5 1.14.5 (source-built), write 18-22 / read 11.5-12.5 GiB/s prior
  reference numbers (re-verify, don't assume identical on new HDF5).
- Prior negative result to respect but not blindly re-run: exhaustive
  ROMIO/striping sweep on the OLD HDF5 found no lever beat the 4KB-transfer
  baseline for this file-per-process/contiguous pattern.
- New lever specifically in scope: HDF5 1.14.5 collective metadata ops
  (`H5Pset_all_coll_metadata_ops`) and any VOL connector option now available
  that wasn't in the prior HDF5 version — this is a targeted, not exhaustive,
  I/O re-test.

**dftracer-optimizer-io**
- Full L1/L2/L3 checklist pass (`dftracer-io-optimization` skill).
- MUST explicitly test `H5Pset_all_coll_metadata_ops(fapl, true)` /
  collective metadata read+write flags on the HDF5 backend and report
  before/after throughput — this is the key delta this session exists to
  answer.
- Re-confirm (don't re-sweep from scratch) that ROMIO hints / striping still
  don't move the needle at 4KB transfer size; a quick 2-3 point spot-check
  against the prior sweep's best/worst points is sufficient corroboration,
  not a full re-sweep.
- Report every candidate considered, applicable or not, with reasoning
  (policy rule 14 — do not silently skip negatives).

**dftracer-optimizer-communication**
- Full L1/L2/L3 checklist pass (`dftracer-communication-optimization` skill).
- Check MPI collective costs around HDF5 collective metadata/IO calls
  (`MPI_Allreduce`/`MPI_Bcast` inside HDF5's collective metadata path),
  barrier placement in IOR's timing harness, rank-to-node mapping /
  process-placement effects at 512 ranks.

**dftracer-optimizer-compute**
- Full L1/L2/L3 checklist pass (`dftracer-compute-optimization` skill).
- Check for unnecessary data verification/checksum compute (`-C` chunk
  option flags in IOR, if any hashing is enabled), buffer fill patterns.

**dftracer-optimizer-memory**
- Full L1/L2/L3 checklist pass (`dftracer-memory-optimization` skill).
- Check IOR's transfer buffer allocation/alignment, HDF5 internal buffer
  sizes (chunk cache, sieve buffer), memory pinning on the MI300A APU
  (shared CPU/GPU memory — no explicit device transfer needed, per system
  notes, so this dimension is likely low-yield; still document the verdict).

**Validation runs**
- Each proposed variant re-run at the SAME 512-rank / 8-node scale as
  baseline, same `dataset/<variant_name>/` PFS output dir, bracketed by
  `session_service_start`/`stop`, at least 1 replicate of the best variant
  to compare against the baseline noise band established in STEP 3.

**Expected artifacts**
- Per-dimension merged optimization report (applied + not-applicable-with-
  reason for every checklist item, per policy rule 14).
- Best-variant config + measured delta vs. baseline noise band.
- Explicit answer to: "does HDF5 1.14.5 collective metadata ops change the
  prior negative conclusion?" yes/no + numbers.

---

## STEP 6: dftracer-report

**Inputs**
- All artifacts from STEP 1-5: annotation coverage, build logs, baseline
  throughput + noise band, analysis/diagnosis findings, 4-dimension
  optimizer report, best-variant validated delta.
- Assemble `final_report/` per policy rule 15: `config.ini` (only place a
  real path/session value goes), `scripts/lib_load_config.sh` sourced by
  every other script, every script actually run during the session copied
  into `scripts/` (not just an automated glob subset).
- Self-contained validation: point `OUTPUT_ROOT` at
  `<WS>/final_folder_validate/` (isolated from real run data), fill in
  `WORKSPACE_ROOT`, run `scripts/run_all.sh <alloc-id>`, confirm it
  reproduces the reported result within the noise band established in
  STEP 3/5 before calling `session_final_report(validated=True, ...)`.

**Expected artifacts**
- `<WS>/final_report/` complete and validated.
- `session_final_report` called with `validated=True` and a one-line
  `validation_notes`.

---

## STEP 7: dftracer-privacy-guard

**Inputs**
- Full workspace `<WS>` including newly written `final_report/`,
  `pipeline_plan.md`, `pipeline_plan_changelog.md`, any skill/memory
  proposals from STEP 1-6.

**Expected artifacts**
- `privacy_scan()` reports `clean` — including the `final_report/` carve-out
  (gitignore blind spot fixed per memory
  `bug-privacy-scan-final-report-gitignore-blindspot`; explicitly re-scan
  `final_report/` even though the session workspace is gitignored).
- Session considered done only once this reports clean.

---

## DISPATCH ORDER

dftracer-annotate-c, dftracer-build-smoke, dftracer-tracer, dftracer-analyzer, dftracer-optimizer, dftracer-report, dftracer-privacy-guard
