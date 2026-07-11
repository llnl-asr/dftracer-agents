# DFTracer Pipeline Plan — h5bench/20260710_061131

## Overview

- **SCOPE NARROWING (2026-07-10, user directive, supersedes the 7-workload plan below):**
  active work is now `read` and `write` ONLY. `append`, `overwrite`, `write_unlimited`,
  `exerciser`, `hdf5_iotest` are deprioritized as-is (traces/data left in place, not deleted,
  not further worked unless the user asks). See `optimization_plan_draft.md`'s
  "CORRECTION + Scope Narrowing" section (bottom of file) for the corrected `read`/`write`
  calibration, diagnosis, and draft L1/L2/L3 optimization plan. Key corrected facts:
  - `read`'s original 5 baseline reps were **silently broken** (h5bench_read requires a
    pre-existing file matching its config's dims; the original scripts passed a fresh empty
    path, producing an HDF5 error-storm that still exited 0). Fixed with a write-then-read
    two-phase script (`baseline/configs/write_for_read.cfg` + `read.cfg`, `DIM_1` shrunk
    33554432→2097152) — traces now in `baseline/traces/raw/read_v2/rep1-5`, confirmed 0 HDF5
    errors, all 5 reps captured.
  - `write.cfg`'s `DIM_1=33554432` was oversized (~1.6-1.7 TB/replicate, 35-40+ min/run,
    verified via `du -sh` on the actual `.h5` file, not trace-log size). Shrunk /16 to
    `DIM_1=2097152` → confirmed 575 GB actual, 12.13 min actual elapsed at 768 ranks (job
    `f84WCJW3a7`). Traces now in `baseline/traces/raw/write_v2/rep1-5`.
  - Old `baseline/traces/raw/read/rep1-5` (broken) and `write/rep1-5` (oversized-config)
    traces are NOT deleted — flagged for user decision (keep/archive/delete).
  - A large volume of loose, un-segregated `baseline_write_rep{1-5}-*.pfw.gz` +
    `calib_8node-*.pfw.gz` files (~510k files, ~1.2 GB) were found directly under
    `baseline/traces/raw/` (not in a `<workload>/<rep>/` subfolder) — confirmed junk (debris
    from repeated retries before the folder-segregation fix; the properly-segregated
    `write/rep{1-5}/` dirs already have the valid, complete 1536-file/rep captures). Deleted
    via `drm` after explicit user confirmation.
  - A runaway job-submission-loop incident occurred during this pass (~3,200 queued
    `h5bench_write` jobs stacked across 5 allocations from an uncontrolled retry loop) —
    cancelled by the coordinator (scoped per-allocation `flux cancel --all`, not global). New
    mandatory rule added to `flux-alloc` skill: always check allocation occupancy
    (`flux jobs -a | grep -cE ' R | PD | S '`) and confirm the previous job in that allocation
    has completed before submitting a new one — never submit in a bare retry loop.

- **App:** h5bench, cloned from `https://github.com/hariharan-devarajan/h5bench` @ `master`
  (fork with dftracer annotation support baked in — per `workload-h5bench` skill).
- **Session workspace:** `$PROJECT_ROOT/workspaces/h5bench/20260710_061131`
  (subdirs: `performance`, `source`, `baseline`, `annotated`, `artifacts`, `tmp`, `dataset`)
- **System:** tuolumne (AMD MI300A APU cluster, Cray PE / ROCm). MPI launcher: `flux run`.
  Modules: craype-x86-trento, libfabric/match_SHS, craype-network-ofi, perftools-base/25.09.0,
  craype/2.7.35, PrgEnv-cray/8.7.0, flux_wrappers/0.1, xpmem/2.6.5, cce/20.0.0,
  cray-libsci/25.09.0, cray-mpich/9.0.1, python/3.13.2.
  Required: `LD_LIBRARY_PATH` must include `/opt/cray/pe/cce/20.0.0/cce/x86_64/lib`,
  `.../lib/default64`, and `/usr/lib64` (dlopen/libdl) — set BEFORE
  `session_install_dftracer` / `session_build_annotated`.

- **SCOPE CORRECTION (confirmed 2026-07-10 by direct source inspection): 7 workloads, not 8.**
  `write_normal_dist` / `h5bench_write_var_normal_dist` does NOT exist anywhere in this fork
  (hariharan-devarajan/h5bench @ master) — no binary target, no config variant, no sample.
  Grepping the whole source tree for `normal_dist`/`var_normal`/`NORMAL` returns nothing. It has
  been removed entirely from this plan's workload list, iteration matrix, and per-workload
  tables. The `workload-h5bench` skill has already been corrected (see its "Binary Names"
  section and the fork-caveat note in "Optimization Loop — Failed Attempts"). **The real set of
  7 workloads is: write, write_unlimited, overwrite, append, read, hdf5_iotest, exerciser.**

- **Flux allocations — FOUR allocations, giving 13 concurrent 8-node/768-rank slots (UPDATED
  2026-07-10; a 4th allocation `<flux-jobid>` came online, raising capacity from 9 to 13 slots):**
  | Slot | Allocation JOBID | Nodes | Sub-block |
  |---|---|---|---|
  | 1 | `<flux-jobid>` | 8 (whole allocation) | slot 1 |
  | 2 | `<flux-jobid>` | 32 (chunk 1/4) | nodes 1-8 → slot 2 |
  | 3 | `<flux-jobid>` | 32 (chunk 2/4) | nodes 9-16 → slot 3 |
  | 4 | `<flux-jobid>` | 32 (chunk 3/4) | nodes 17-24 → slot 4 |
  | 5 | `<flux-jobid>` | 32 (chunk 4/4) | nodes 25-32 → slot 5 |
  | 6 | `<flux-jobid>` | 32 (chunk 1/4) | nodes 1-8 → slot 6 |
  | 7 | `<flux-jobid>` | 32 (chunk 2/4) | nodes 9-16 → slot 7 |
  | 8 | `<flux-jobid>` | 32 (chunk 3/4) | nodes 17-24 → slot 8 |
  | 9 | `<flux-jobid>` | 32 (chunk 4/4) | nodes 25-32 → slot 9 |
  | 10 | `<flux-jobid>` | 32 (chunk 1/4) | nodes 1-8 → slot 10 |
  | 11 | `<flux-jobid>` | 32 (chunk 2/4) | nodes 9-16 → slot 11 |
  | 12 | `<flux-jobid>` | 32 (chunk 3/4) | nodes 17-24 → slot 12 |
  | 13 | `<flux-jobid>` | 32 (chunk 4/4) | nodes 25-32 → slot 13 |
  `<flux-jobid>` confirmed RUNNING with ~24h remaining as of 2026-07-10 (verify via `flux jobs`
  before use). Every job is `-N 8 -n 768`. Track slot occupancy explicitly — never submit into
  an occupied slot; confirm the sub-block node-range flag with `flux run --help` before first
  concurrent use.

- **Confirmed build recipe (2026-07-10 — baseline build succeeds, all 7 binaries present at
  `<WS>/build/h5bench_*`, correctly linked against the source-built parallel HDF5
  `<WS>/hdf5_1.14/lib/libhdf5.so`, NOT `/usr/lib64`). Required CMake flags:**
  ```bash
  export HDF5_HOME=<WS>/hdf5_1.14   # h5bench CMakeLists.txt ~L56-64 reads this env var
  cmake -S source -B build \
    -DH5BENCH_EXERCISER=ON -DH5BENCH_METADATA=ON \
    -DHDF5_ROOT=<WS>/hdf5_1.14 -DHDF5_PREFER_PARALLEL=ON \
    ...
  ```
  - **Do NOT use `-DH5BENCH_ALL=ON`** — it also pulls in AMReX/E3SM/OpenPMD/MACSio, which
    require external submodules not present in this shallow clone and will fail configure.
  - **Always use a fresh `build/` dir (no stale CMake cache)** — otherwise CMake's `FindHDF5`
    silently resolves to the system serial HDF5 at `/usr/lib64` via `/usr/bin/h5cc` on `PATH`,
    even with `HDF5_ROOT` set, if a prior cache entry already pinned the wrong HDF5.
  - This exact recipe applies to BOTH the baseline build (STEP 1, already done) and the
    annotated build (STEP 3) — baked into STEP 3 below so it isn't rediscovered by trial/error.

- **R9 rule (OS-cache-busting) + minimum run-length rule (MANDATORY, all 7 workloads):**
  1. Each run must move **>4016 GiB** total (502 GiB/node × 8 nodes) through the filesystem.
  2. Each run must last **≥10 minutes** of actual I/O — a run finishing sooner is inside
     run-to-run noise. If a workload's R9-sized run finishes <10 min, scale `TIMESTEPS` (or
     the workload's native step-count knob) up further — do not accept a short run.
  3. **REPLICATE FLOOR (MANDATORY):** every baseline config AND every optimization iteration
     config — not just the eventual "best"/final ones — must be run a **minimum of 5
     replicates**. See the "Replicate + CV + percentile policy" block below for the full rule.
     This is a hard floor: if scope needs trimming to fit the time budget, trim the NUMBER OF
     DISTINCT CONFIGS (variants) tried, never the replicate count per variant that IS tried.
  These are stated per-workload below and re-stated in STEP 4/STEP 6.

- **Replicate + CV + percentile policy (MANDATORY, applies to STEP 4 AND STEP 6, every config,
  every workload):**
  1. **Floor: 5 replicates per config.** A "config" = one distinct (workload × axis-setting)
     combination — a baseline config counts as one config, each optimization iteration counts
     as its own config. Single-run "point estimates" are not acceptable anywhere in this plan,
     including intermediate/non-final iterations.
  2. **Coefficient of Variation (CV) trigger:** after 5 replicates, compute
     `CV = stddev(metric) / mean(metric)` on the workload's PRIMARY throughput/bandwidth metric
     (GB/s achieved, or workload-appropriate equivalent). **CV threshold: 10-15%.**
     - `CV ≤ 10%`: stable at 5 — stop.
     - `10% < CV ≤ 15%`: borderline — extend to 8 replicates, recompute CV; stop if it drops
       ≤15% at 8, otherwise extend to 10 and stop there regardless of CV (practical ceiling for
       this session's time budget — record the residual CV honestly rather than chasing it
       further).
     - `CV > 15%` at 5: extend directly to 8, then 10 if still unstable, same stop rule as above.
  3. **Report with percentiles, never a bare number.** Every config's result is reported as
     `median (p50) / p95 / min / max` (and the achieved replicate count + CV) — not a single
     sample. `n` GB/s alone is not a valid result anywhere downstream of STEP 4.
  4. **Any "X% improvement" claim is percentile-based.** STEP 6's comparator computes deltas as
     `median_variant vs median_baseline` AND `p95_variant vs p95_baseline` — report both
     (e.g. "median throughput improved 18%, p95 improved 9%"). A delta computed from single
     samples on either side is not a valid claim and must not appear in the final report.
  5. **Outlier awareness:** Lustre-contention/network-noise outliers are exactly what the
     replicate floor + percentile reporting exists to absorb — do not hand-drop an "unlucky"
     replicate; let the percentile computation do its job. If a replicate clearly failed
     (crash, wrong config applied, truncated run), exclude it and re-run to restore the floor
     of 5 valid replicates, and note the exclusion + reason in the artifact log.

- **Config provenance rule (MANDATORY):** every workload's config is derived from an existing
  file under `source/samples/` (h5bench's own JSON examples for the Python/JSON runner) or
  `source/metadata_stress/hdf5_iotest.ini` (the native INI for `hdf5_iotest`) — never invented
  from scratch. Where multiple sample variants exist for a workload, the **most complex /
  production-representative** one is chosen (chunked/compressed over plain contiguous,
  multi-dim over 1D, interleaved/strided over trivially-contiguous, collective over
  independent) — not the simplest example. The chosen sample is then scaled up (DIM_1/DIM_2/
  TIMESTEPS/steps/arrays/etc., pattern keys preserved) to hit the R9 size + ≥10 min duration
  targets. This deliberately maximizes access-pattern VARIETY across the 7 workloads rather
  than collapsing everything to one generic 1D contiguous template.

  **Chosen sample + rationale per workload** (all paths relative to `source/`):

  | # | Workload | Binary | Chosen sample | Pattern picked up | Why this one over the alternatives |
  |---|---|---|---|---|---|
  | 1 | write | `h5bench_write` | `samples/sync-write-2d-interleaved-interleaved.json` | 2D, `MEM_PATTERN=INTERLEAVED`, `FILE_PATTERN=INTERLEAVED`, `COLLECTIVE_DATA/METADATA=YES` | Samples include 1D/2D/3D × contig/interleaved/strided combos; picked the 2D **interleaved+interleaved+collective** variant over the many plain `contig-contig` ones because non-contiguous multi-dim scattered access is the more production-representative I/O pattern to stress-test, and it's still collective (not independent). (3D-contig-contig was considered but rejected — 3D but still trivially contiguous, i.e. less demanding than 2D interleaved.) |
  | 2 | read | `h5bench_read` | `samples/sync-write-1d-contig-contig-read-strided.json` (verify exact filename/keys — sibling of `-read-partial`/`-read-full`; STRIDED read option chosen) | `READ_OPTION=STRIDED` | Samples offer `read-full`, `read-partial`, `read-strided` variants; `FULL` is the trivial case (reads the whole dataset linearly) — `STRIDED` exercises non-contiguous read access, which is the harder/more representative case for diagnosing read bottlenecks. Rejected the 3D-contig-contig-read-full sample (multi-dim but only supports `FULL` reads in that pairing) in favor of the harder access pattern over the harder dimensionality, since read-path stress is the goal for this workload's baseline. |
  | 3 | append | `h5bench_append` | `samples/sync-append.json` (only append sample shipped — pairs `write-unlimited` + `append` stages) | `READ_OPTION=FULL` (append reads-then-appends), `COLLECTIVE_DATA=YES`, `COMPRESS=YES` on the seed write-unlimited stage | Only one append sample exists; it is already the most complex option available (compressed seed data + collective append) — no alternative to select against. |
  | 4 | overwrite | `h5bench_overwrite` | `samples/sync-overwrite.json` (only overwrite sample shipped) | `READ_OPTION=FULL`, `COLLECTIVE_DATA/METADATA=YES` | Only one overwrite sample exists — already collective, so no simpler/more-complex alternative to weigh. |
  | 5 | write_unlimited | `h5bench_write_unlimited` | `samples/sync-write-unlimited.json` (only sample; also used as append's seed stage) | `COMPRESS=YES`, `MODE=SYNC`, extendable dim | Only one sample exists and it already includes compression (`COMPRESS=YES`) — satisfies the "prefer compressed" preference by default, no alternative needed. |
  | 6 | hdf5_iotest | `h5bench_hdf5_iotest` | `metadata_stress/hdf5_iotest.ini` (native INI for the direct binary — NOT `samples/sync-metadata.json`, which wraps the same test through the JSON/Python runner rather than the binary's own CLI) | `dataset-rank=4` (4D — already the most complex dimensionality this tool supports), `scaling=weak`, `layout=contiguous`, `mpi-io=independent` | This is the tool's own shipped INI, already 4D/weak-scaling; `layout=contiguous`/`mpi-io=independent` are its defaults — STEP 6 Axis A/B explicitly tests `layout=chunked` and `mpi-io=collective` as the "more complex" alternates the tool itself exposes, rather than picking a nonexistent more-complex baseline sample. |
  | 7 | exerciser | `h5bench_exerciser` | `samples/sync-exerciser.json`, **3rd benchmark block** (`indepio=true, addattr=true, usechunked=false, keepfile=false`) — NOT the 1st (bare defaults) or 2nd (`indepio` only) block | 2D (`numdims=2`), independent I/O, attribute writes enabled | The sample ships 3 variants of increasing flag count; the 3rd sets the most flags (`indepio`+`addattr`+explicit `usechunked`/`keepfile`) and is the most representative/complete exerciser invocation shipped — picked over the bare-defaults and `indepio`-only blocks. |

- **R9-sized + ≥10min-duration numeric targets per workload** — unchanged from prior revision;
  see STEP 4 for the per-workload config table. The tracer agent MUST verify exact per-rank vs.
  global sizing semantics against h5bench source/`--help` before locking final numbers.

- **Binary names:** see workload table above. Verify with `ls <WS>/annotated/build_ann/h5bench_*`
  before every run — labels don't always match binary names.
- **Dependency:** `read`/`append`/`overwrite` operate on `write`'s output file — sequence
  `write` first (STEP 4 early wave), then `read`/`append`/`overwrite` (later wave).

- **Total iteration / run budget (REVISED — 7-workload scope + 9-slot concurrency):**
  The 5-replicate floor multiplies total run count ~5x per data point. To keep this practical
  the NUMBER OF DISTINCT CONFIGS (variants) is trimmed; the replicate count per config tried is
  never trimmed below 5.

  - **Baseline configs: 7** (one per workload, no A/B/C variation at baseline).
    **× 5 replicates minimum = 35 baseline runs minimum.** More if CV forces 8 or 10 replicates
    on any workload.
  - **Optimization configs — TRIMMED variant count:**
    - `write`: **4 variants** (A-only, B-only, C-only, combined-best).
    - 6 remaining workloads (`read`, `append`, `overwrite`, `write_unlimited`, `hdf5_iotest`,
      `exerciser`): **3 variants each** (A-only, B-only, combined-best; C-axis folded into
      "combined" unless STEP 5 flags stripe as the dominant bottleneck for that workload, in
      which case swap one slot for C-only) = 18 variants.
    - Literature/Axis-D (STEP 6-PRE): **capped at 1 iteration per workload** where the
      literature pass finds something concretely testable and not already covered/blocked by
      the DO-NOT-RETRY list = **up to 7 variants** (one per workload).
    - **Total optimization variants: up to 29** (4 + 18 + 7).
    - **× 5 replicates minimum = up to 145 optimization runs minimum**, more if CV forces 8/10
      replicates on any variant.
  - **Grand total minimum run count: 35 + 145 = 180 runs.** This is the practical floor; actual
    will be higher wherever a workload's CV exceeds 15% at 5 and needs 8 or 10 replicates.
  - **Wave estimate at 13 concurrent slots (UPDATED 2026-07-10 — previously computed at 9
    slots; the 4th allocation `<flux-jobid>` raises capacity from 9 to 13 slots):**
    - Baseline: `35 runs / 13 slots = ceil(35/13) = 3 waves` minimum, respecting the
      write→{read,append,overwrite} dependency (write's 5 replicates must complete before
      those 3 workloads' replicate waves can start). In practice: wave 1 covers write's 5
      replicates (slots 1-5) concurrently with the first 8 of the 3 independent workloads'
      (write_unlimited, hdf5_iotest, exerciser; 15 runs total) replicates in slots 6-13,
      wave 2 covers the remaining 7 of those 15, then wave 3 covers read/append/overwrite's 15
      runs once write's output file is ready (`ceil(15/13) = 2 waves`, but overlaps with wave 2
      slot availability in practice, so 3 waves total is the realistic floor).
    - Optimization: `145 runs / 13 slots = ceil(145/13) = 12 waves` minimum.
    - **Total ≈ 15 waves minimum** (down from ≈21 at the previous 9-slot estimate, and from
      ~42-44 at the original stale 5-slot/8-workload estimate), each wave bounded by its
      slowest job (every individual job is still sized to run ≥10 min per the R9 rule above).
      Waves needing CV-driven replicate extension (8 or 10 instead of 5) add proportionally
      more waves for just that config, not for the whole plan.
- **Known pitfalls to avoid globally** (full detail in `workload-h5bench` / `software-mpi`):
  - Cray HDF5 `chid_t` typo in `H5Apublic.h` breaks the C++ frontend (brahma) — patch if not
    already applied.
  - dftracer installs as `libdftracer_core.so`, not `libdftracer.so` — patch CMake link line.
  - `DFTRACER_C_INIT(NULL, NULL, -1)` segfaults — must be `DFTRACER_C_INIT(NULL, NULL, NULL)`.
  - **`DFTRACER_DATA_DIR=all`** always.
  - **ROMIO hints via `--env` to `flux proxy flux run` silently drops the binary and args** —
    always use a bash wrapper script (`export ...; exec "$@"`), never inline.
  - `MPICH_MPIIO_HINTS` uses **colons** between `key=value` pairs, commas between patterns.
  - **Reusing an output directory inflates `__lxstat` latency ~100×** — every job writes to its
    own fresh output dir/wrapper/trace-log prefix, EXCEPT `read`/`append`/`overwrite`, which
    deliberately reuse `write`'s output FILE (that's the point) but still get their own
    trace-log prefix and must not truncate that file between replicate waves.
  - Cray MPICH **accepts but ignores** `cb_nodes` unless paired with `CRAY_CB_NODES_MULTIPLIER`.
  - **`-DH5BENCH_ALL=ON` fails configure** — pulls in AMReX/E3SM/OpenPMD/MACSio submodules not
    present in this shallow clone; use `-DH5BENCH_EXERCISER=ON -DH5BENCH_METADATA=ON` instead.
  - **Stale CMake cache silently re-resolves HDF5 to system serial `/usr/lib64`** even with
    `HDF5_ROOT`/`HDF5_HOME` set — always configure into a fresh `build/`/`build_ann/` dir.
- **Analysis:** `cluster_n_workers=32` for dfanalyzer (never `cluster_cores`).
- **Logging:** every log goes to `<WS>/artifacts/<NN>_<step>.log`.
- **Profiling:** every step agent brackets its work with `profile_step_begin`/
  `profile_step_end` using the exact `## STEP N: <agent-name>` heading below.
- **cwd rule:** all build/run/smoke-test commands execute with cwd inside the session
  workspace, never the project root.
- **Self-learning note (informational only, no action here):** per Pipeline Policy rule 10,
  each executing agent records genuinely new findings (h5bench config-schema specifics for
  `hdf5_iotest`/`exerciser`, any new ROMIO/Lustre findings at 8-node scale, actual per-rank vs.
  global sizing semantics discovered at runtime, literature-sourced optimization techniques
  tried and measured, and actual observed CV per workload/config, since that tells future
  sessions whether 5 replicates is typically enough for a given workload's I/O pattern or
  whether it reliably needs 8-10) into the relevant skill/agent-YAML/MCP tool as it runs
  STEP 2-6 — this happens organically during execution, not in this plan file.

---

## STEP 1: dftracer-session-setup

**Status: ALREADY DONE.**

- `run_id = "h5bench/20260710_061131"`
- Workspace: `$PROJECT_ROOT/workspaces/h5bench/20260710_061131`
- `source/` — pristine clone of h5bench @ master. Confirmed present: `source/samples/*.json`
  (JSON runner examples) and `source/metadata_stress/hdf5_iotest.ini` (native INI) — both are
  the config provenance sources for STEP 4/6, see Overview table.
- `baseline/` — unannotated build tree (`session_get_run_paths(run_id, "baseline")`):
  `source_dir`, `traces_raw`, `traces_compact`, `scripts_dir`, `dftracer_log_prefix`.
- **CONFIRMED (2026-07-10): baseline build succeeds with the recipe in the Overview
  ("Confirmed build recipe" block) — all 7 binaries present at `<WS>/build/h5bench_*`, linked
  against `<WS>/hdf5_1.14/lib/libhdf5.so`.**
- `annotated/` — target for annotated source tree (STEP 2 writes here)
- `dataset/` — target output dir for h5bench `.h5` files (Lustre-backed)
- Build system: CMake. Confirm HDF5 `chid_t` patch already applied via
  `grep chid_t <hdf5_prefix>/include/H5Apublic.h` (should return nothing) before STEP 2.
- If HDF5 was NOT yet built from source, do so before proceeding (never Cray/system module).

---

## STEP 2: dftracer-annotate-c

**Inputs:**
- Language: C. Source tree to annotate: copy `baseline/source` into `annotated/source`.
- Exclude patterns: none known yet.
- Smoke-test target: single-process `h5bench_write` with small INI (unrelated to the
  production sample configs above — this is purely a build/link sanity check):
  ```ini
  MEM_PATTERN=CONTIG
  FILE_PATTERN=CONTIG
  TIMESTEPS=2
  DELAYED_CLOSE_TIMESTEPS=0
  COLLECTIVE_DATA=NO
  COLLECTIVE_METADATA=NO
  NUM_DIMS=1
  DIM_1=1048576
  DIM_2=1
  DIM_3=1
  ```
  `mpirun -np 1 ./h5bench_write /tmp/h5bench_smoke.cfg /tmp/smoke_out.h5`

**Known annotation pitfalls:** `assert()` macro brace corruption, stray `{` before `else if`,
use `DFTRACER_C_FUNCTION_START`/`END`, `DFTRACER_C_INIT(NULL, NULL, NULL)`.

**Expected artifact:** annotated source under `annotated/source/`, compile-checked with
`clang_syntax_check` (Cray MPICH `extra_include_dirs`) for ALL 7 binaries' source files.

---

## STEP 3: dftracer-build-smoke

**Inputs:**
- Build into `annotated/build_ann/` using the **confirmed build recipe** (see Overview):
  ```bash
  export HDF5_HOME=<WS>/hdf5_1.14
  cmake -S annotated/source -B annotated/build_ann \
    -DH5BENCH_EXERCISER=ON -DH5BENCH_METADATA=ON \
    -DHDF5_ROOT=<WS>/hdf5_1.14 -DHDF5_PREFER_PARALLEL=ON \
    -DMPI_C_WORKS:BOOL=TRUE -DMPI_CXX_WORKS:BOOL=TRUE ...
  ```
  - Use a **fresh** `build_ann/` dir (no stale cache) — do not reuse a partially-configured
    directory, or `FindHDF5` may silently resolve to system serial HDF5 at `/usr/lib64`.
  - **Do NOT use `-DH5BENCH_ALL=ON`** (pulls in unavailable AMReX/E3SM/OpenPMD/MACSio
    submodules and fails configure).
- Link `-ldftracer_core` (not `-ldftracer`):
  ```bash
  sed -i 's/-ldftracer\b/-ldftracer_core/g' build_ann/CMakeCache.txt
  find build_ann/CMakeFiles -name "link.txt" -exec sed -i 's/-ldftracer\b/-ldftracer_core/g' {} \;
  grep -r "ldftracer[^_]" build_ann/   # must return nothing
  ```
  Append at END of the link line.
- `DFTRACER_INIT=FUNCTION`. Smoke command: single-process `h5bench_write` only (shared build
  target/link flags cover all 7 binaries; STEP 4 discovers any per-binary build issue).
- Verify exit 0 AND non-empty `<prefix>-*-app.pfw.gz`.

**Expected artifact:** ALL 7 binaries present under `annotated/build_ann/h5bench_*` (record
which built successfully — flag any missing binary as a blocker for STEP 4). Build log at
`<WS>/artifacts/03_build_annotated.log`.

---

## STEP 4: dftracer-tracer

**MANDATORY per-workload requirements (apply to all 7):**
1. Build each workload's INI/config by taking the chosen sample from the Overview table and
   scaling it per the numeric targets in the prior revision — do not invent keys not present
   in the sample (except where the Overview table explicitly calls out a verify-via-`--help`
   gap, i.e. `hdf5_iotest`, `exerciser`).
2. Confirm total I/O volume ≥4016 GiB (target ≥4032 GiB as computed) before submitting.
3. Confirm/target run duration ≥10 minutes; if a dry-run or the first minute of a run
   indicates it will finish sooner, kill it and re-launch with `TIMESTEPS` (or the workload's
   native step-count knob) scaled up.
4. **Replicate floor (MANDATORY, applies to every one of the 7 baseline configs — not just a
   "final" one): run a MINIMUM of 5 replicates of each workload's baseline config.** After 5,
   compute CV on the achieved GB/s (or workload-appropriate primary metric) across the 5 runs:
   - `CV ≤ 10%`: stop at 5.
   - `10% < CV ≤ 15%`: extend to 8, recompute; stop if ≤15%, else extend to 10 and stop there
     regardless (record residual CV honestly).
   - `CV > 15%` at 5: extend directly to 8, then 10 if still unstable.
   Report each baseline's result as `median (p50) / p95 / min / max` + achieved replicate count
   + CV — never a bare single number. This is the number STEP 6's comparator uses; a
   single-sample baseline is not a valid comparison point.

**Sequencing (read/append/overwrite depend on write's output file — 3 waves minimum for
baseline replication at 13 concurrent slots, UPDATED 2026-07-10 from the previous 9-slot/4-wave
estimate, now that the 4th allocation `<flux-jobid>` adds slots 10-13):**
- **Wave BW1:** `write` × 5 replicates in slots 1-5, running CONCURRENTLY with the first 8 of
  the 3 independent-of-write workloads' (`write_unlimited`, `hdf5_iotest`, `exerciser`)
  replicates (15 runs total needed) in slots 6-13. If write's CV > 10% after 5, launch 3 more
  into a follow-on wave using freed slots.
- **Wave BW2:** remaining 7 replicates of `write_unlimited`/`hdf5_iotest`/`exerciser` (8
  already started in BW1, 7 remain) using freed slots — fits in a single wave at 13 slots.
- **Wave BW3 (after write's ≥5 replicates all complete — poll `flux jobs` on write's
  slots):** `read`, `append`, `overwrite` — each needs its own 5 (then possibly 8/10)
  replicates against write's output file. 3 workloads × 5 replicates = 15 runs =
  `ceil(15/13) = 2 waves` in the worst case, but typically overlaps with BW2's slot
  drain, so 1 additional wave (BW3) is the realistic floor.
- **Total baseline waves: ≈ 3 waves minimum** — exact wave count depends on real run
  durations; poll rather than assume a fixed schedule.

**Output dirs:** fresh subdir per replicate, e.g. `<WS>/dataset/baseline_<workload>_rep<N>/`
for `N` in 1..5 (then 6-8, 9-10 if CV forces extension). `read`/`append`/`overwrite` read/mutate
`write`'s file but log to their own `<WS>/dataset/baseline_<workload>_rep<N>/` for
workload-specific output/metadata.

**dftracer env vars (wrapper script only, per workload replicate):** `DFTRACER_ENABLE=1`,
`DFTRACER_INIT=FUNCTION`, `DFTRACER_DATA_DIR=all`,
`DFTRACER_LOG_FILE=<WS>/baseline/traces/raw/baseline_<workload>_rep<N>`,
`DFTRACER_INC_METADATA=1`. No ROMIO hints for any baseline.

**Wrapper script template** (`<WS>/baseline/scripts/run_baseline_<workload>.sh`):
```bash
#!/bin/bash
set -euo pipefail
export DFTRACER_ENABLE=1
export DFTRACER_INIT=FUNCTION
export DFTRACER_DATA_DIR=all
export DFTRACER_INC_METADATA=1
export DFTRACER_LOG_FILE="$1"; shift
exec "$@"
```
Launch each with `run_in_background: true`, poll `flux jobs` per slot rather than blocking.

**`hdf5_iotest` / `exerciser` specifically:** before launching, run `--help` (or inspect via
`graph_query`/source read of `main()`) to confirm the scaled config in the Overview table
actually maps to valid flags/INI keys, and to resolve the weak-scaling `process-rows ×
process-columns` factorization for 768 ranks (`hdf5_iotest`) and the `nsizes`/`minels` doubling
semantics (`exerciser`). Record the ACTUAL invocation used, since it will differ from the
Overview table's first-approximation numbers, and reuse the SAME resolved invocation across all
5-10 replicates of that workload (do not re-derive it per replicate).

**Split traces** after each run into `baseline/traces/compact/<workload>/rep<N>/`.

**Expected artifacts:**
- `baseline/traces/raw/baseline_<workload>_rep<N>-*-app.pfw.gz` for all 7 workloads × (5-10
  replicates each depending on CV).
- `baseline/traces/compact/<workload>/rep<N>/` split output for all replicates.
- Per workload: exit codes, wall time, achieved GB/s per replicate, actual I/O volume,
  **replicate count actually used, computed CV, and median/p95/min/max** recorded in
  `<WS>/artifacts/04_tracer_baseline.log`. This median/p95 pair (not any single replicate) is
  the fixed baseline comparison point STEP 5/STEP 6 must use.

---

## STEP 5: dftracer-analyzer / dftracer-diagnoser

**Approach: 7 SEPARATE per-workload diagnoses**, plus a combined roll-up summary table
afterward (each workload's I/O pattern is distinct — write-only, read-only, metadata-heavy
append, in-place overwrite, unlimited-dim writes, and two upstream-native benchmarks — not
comparable on one merged view).

**Inputs:**
- Preset: POSIX/MPI-IO view (not DLIO/ML preset).
- `cluster_n_workers=32` (never `cluster_cores`).
- Views per workload: bandwidth/throughput summary, POSIX call-latency breakdown (open/close/
  pwrite/pread/lseek/lxstat means and p50/p95/p99), small-vs-large I/O size histogram
  (`small_io_pct`), and for read-dominated workloads also `rand_pct`/read-ahead effectiveness.
- Checkpoint dir: `<WS>/baseline/traces/compact/<workload>/` (across all replicates — use the
  median-CV replicate, or aggregate, per the analyzer tool's own capability; do not silently
  pick replicate #1 as "the" trace to analyze without noting that choice).
- Use STEP 4's median/p95/min/max (not a single run) to state the noise band before flagging a
  workload's bottleneck as significant (a "bottleneck" inside the replicate noise band, i.e.
  within the observed CV, is not real).

**Expected artifact:** ranked bottleneck list per workload at
`<WS>/artifacts/05_diagnose_<workload>.log` (7 files) plus roll-up at
`<WS>/artifacts/05_diagnose_summary.log`, each workload flagged "optimize" or "skip" — feeds
STEP 6, but note per STEP 6's literature-search requirement below: a "skip" flag from this
diagnostic step does NOT automatically skip the literature pass — it only skips the
diagnostic-driven A/B/combined sweep.

---

## STEP 6: dftracer-optimizer

**MANDATORY first action (diagnostic KB):** load `workload-h5bench` "Optimization Loop —
Failed Attempts" / "✅ WORKING" and `software-mpi`'s "Failed Configurations" + `cb_nodes`
guidance before proposing any config, for EVERY workload.

### STEP 6-PRE: Literature + knowledge-base search (MANDATORY, runs BEFORE the iteration
matrix below, for ALL 7 workloads — including any flagged "skip" by STEP 5's diagnosis)

1. **`opt_kb_lookup` against the existing optimization knowledge base FIRST** — query for
   `h5bench`, `HDF5`, `Lustre`, `ROMIO`, and each of the 7 workload labels specifically.
2. **Literature pass** — for each workload, run `search_arxiv`, `search_semantic_scholar`, and
   `session_search_optimization_papers` (and `rag_search`/`session_search_optimization_context`
   if available) for parallel HDF5/MPI-IO/Lustre optimization techniques matching THAT
   workload's specific access pattern (per-workload query breakdown: write = 2-phase
   aggregation/subfiling/topology placement; read = read-ahead/prefetch/collective reordering;
   append/overwrite = in-place update/metadata caching; write_unlimited = chunk-cache/
   compression pipeline; hdf5_iotest = MDT-aware striping/collective metadata batching;
   exerciser = attribute caching/independent I/O batching; plus a generic async-I/O-middleware
   search noting this session's binaries are SYNC builds).
3. **Cross-reference every literature finding against the DO-NOT-RETRY list below** before
   proposing it as an iteration — a stack-specific confirmed-failed result overrides general
   literature guidance.
4. **Slot literature-sourced techniques into axis A/B/C where they fit; otherwise give them
   Axis D (structural/middleware).** **Axis D is capped at 1 iteration per workload**, only
   where STEP 6-PRE found something concretely testable in scope.
5. **Every literature-sourced technique tried and measured — pass or fail — is recorded into
   the optimization KB via `opt_kb_record` with its citation**, using the median/p95 of its
   own 5(+)-replicate run, exactly like every other iteration below.

**Output of STEP 6-PRE:** a per-workload short list (max 1 actionable Axis-D candidate per
workload) of literature-sourced techniques, pre-screened against the failed/working KB.
Record this list in `<WS>/artifacts/06_literature_search.log` before starting any iteration.

**DO NOT RETRY (confirmed failed, root cause understood — applies to all 7 workloads):**
- `DELAYED_CLOSE_TIMESTEPS > 0` — segfault on multi-node MPI runs.
- `H5Pset_alignment` — regression on Lustre + ROMIO.
- `romio_no_indep_rw=enable` — unrecognized hint on cray-mpich 9.0.1.
- `cb_nodes=<stripe_count>` WITHOUT `CRAY_CB_NODES_MULTIPLIER` — accepted but IGNORED.
- `cb_buffer_size=128MB` alone — −16% regression.
- `lfs setstripe -c 28` for the **contiguous `write` workload** specifically.
- `--env MPICH_MPIIO_HINTS=...` directly to `flux proxy flux run` — drops the binary.
- `;`/`,` as MPICH_MPIIO_HINTS separators — must be colons.
- **Do NOT assume `write`'s 16-OST / `romio_cb_write` findings transfer to read-heavy
  workloads.** `read` should be tested with `romio_cb_read`, and its own stripe-count sweep.
- **`write_normal_dist` does not exist in this fork — do not attempt to build, config, or run
  it under any name.** (Confirmed 2026-07-10; see Overview scope correction.)

### TRIMMED iteration matrix (variant count trimmed to accommodate the 5-replicate floor —
replicate count per variant is NEVER trimmed below 5)

**write — 4 variants:**

| # | Workload | Variant | Config |
|---|---|---|---|
| 1 | write | A-only | independent instead of collective (contrast with baseline) |
| 2 | write | B-only | `romio_cb_write=enable:romio_ds_write=disable` (+ `cb_nodes=16:CRAY_CB_NODES_MULTIPLIER=2` if STEP 5 shows aggregator imbalance) |
| 3 | write | C-only | stripe target from STEP 5's `write` diagnosis |
| 4 | write | combined-best | best of 1-3 |

**6 remaining workloads — 3 variants each (18 total):**

| # | Workload | Variant | Config |
|---|---|---|---|
| 5 | read | A-only or C-only (whichever STEP 5 flags dominant) | `READ_OPTION` toggle or stripe target |
| 6 | read | B-only | `romio_cb_read=enable` (+`romio_ds_read=disable` if sieving overhead shown) |
| 7 | read | combined-best | best of 5-6 |
| 8 | append | A-only or C-only | `COLLECTIVE_DATA` toggle or stripe target |
| 9 | append | B-only | workload-appropriate hint |
| 10 | append | combined-best | best of 8-9 |
| 11 | overwrite | A-only or C-only | `COLLECTIVE_DATA/METADATA` toggle or stripe target |
| 12 | overwrite | B-only | workload-appropriate hint |
| 13 | overwrite | combined-best | best of 11-12 |
| 14 | write_unlimited | A-only or C-only | `COMPRESS`/chunk-size variant or stripe target |
| 15 | write_unlimited | B-only | `romio_cb_write=enable:romio_ds_write=disable` |
| 16 | write_unlimited | combined-best | best of 14-15 |
| 17 | hdf5_iotest | A-only or C-only | `layout=chunked` or stripe target |
| 18 | hdf5_iotest | B-only | `mpi-io=collective` |
| 19 | hdf5_iotest | combined-best | best of 17-18 |
| 20 | exerciser | A-only or C-only | `usechunked=true` or stripe target |
| 21 | exerciser | B-only | per STEP 5's MPI-IO hint finding |
| 22 | exerciser | combined-best | best of 20-21 |

**Literature-sourced (Axis D, up to 1 per workload = up to 7 total):** populated at execution
time from STEP 6-PRE's log; each row: workload, citation, config, hypothesis. Not enumerable
in advance.

**Total: up to 29 variants** (4 + 18 + 7). Each variant runs **≥5 replicates**, CV-gated to
8/10 exactly as in STEP 4. Report every variant's median/p95/min/max + replicate count + CV.

### Concurrency / slot execution plan

- Same 13 slots/allocations as STEP 4 (see Overview allocation table, now including
  `<flux-jobid>` slots 10-13). Never submit into an occupied slot — poll
  `flux jobs -no "{id} {state}"` per slot before reuse.
- Each variant's 5 (or 8/10) replicates fill just over a third of a wave at 13 slots; with up
  to 29 variants and up to 145 total runs this is **`ceil(145/13) = 12 waves` minimum** for the
  5-replicate case (down from 17 at 9 slots), more wherever CV forces 8 or 10. Order: write's 4
  variants first, then the 18 new-workload variants, then the up to 7 literature variants last
  (their content is only known after STEP 6-PRE completes).
- Every iteration: own output dir per replicate (`<WS>/dataset/opt_iter<N>_rep<M>/`), own
  wrapper script, own `DFTRACER_LOG_FILE` prefix — except `read`/`append`/`overwrite`, which
  point at `write`'s baseline `.h5` file as input but still get their own trace-log prefix/
  output-dir per replicate.
- Each iteration must ALSO satisfy the R9 + ≥10 min rules from STEP 4 (same config sizing as
  that workload's baseline unless the knob under test is itself part of sizing).

### Per-variant requirements

- Comparator tool run against that workload's own STEP 4 baseline **median/p95** (not a single
  baseline number); record **median delta AND p95 delta**, with axis attribution (including
  "D" for literature-sourced, with citation).
- **Record every result — pass or fail — as a proposed `workload-h5bench` skill update AND via
  `opt_kb_record`** (date, workload, filesystem, axis varied, config, replicate count used, CV
  observed, median/p95/min/max result, root cause, do-not-use-when, and for literature-sourced
  rows: citation), surfaced to the user for confirmation before persisting to the skill.
- The declared best config per workload already carries its own 5(+)-replicate measurement —
  no separate "replicate the best config" pass is needed.

**Expected artifact:** a ranked table of all variants actually run (diagnostic-driven up to 22
+ literature-sourced up to 7 = up to 29) per workload/axis, each with replicate count, CV,
median/p95/min/max, and median+p95 delta vs. that workload's own replicate-confirmed baseline,
a declared best config per workload (or "baseline is already best" for skipped workloads) with
axis attribution including any literature-sourced technique and its citation, and a proposed
skill/KB update surfaced to the user for confirmation. Report actual replicate counts used per
config (flagging any that needed 8 or 10 due to CV) alongside actual variant count run vs.
budgeted.

---

## STEP 7: dftracer-privacy-guard

**Inputs:** `privacy_scan()` against `pipeline_plan.md`, `pipeline_plan_changelog.md`, all
skill/KB-update proposals from STEP 6 (including literature citations — citations themselves
are exempt from redaction per Pipeline Policy rule 9's public-bibliography exception, but any
session-specific provenance around them is not), and all files under `<WS>/artifacts/`,
`<WS>/performance/`.

**Checks:** no usernames/real names/emails, no absolute paths containing `/usr/WS2/<user>/`,
`/p/lustre5/<user>/`, `/g/g92/<user>/` in anything destined for a git-tracked skill/lesson
file (the live workspace itself is gitignored and may keep real paths), no flux job IDs or
session UUIDs in anything persisted to skills.

**Expected artifact:** `privacy_scan()` reports `clean`. Final mandatory step before the
pipeline is considered done. The orchestrator calls `profile_report()` a few seconds after
this step ends.

---

## DISPATCH ORDER

dftracer-annotate-c, dftracer-build-smoke, dftracer-tracer, dftracer-analyzer, dftracer-diagnoser, dftracer-optimizer, dftracer-privacy-guard
