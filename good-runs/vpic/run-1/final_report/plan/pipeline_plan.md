# DFTracer Pipeline Plan — vpic_kokkos/20260714_155730

## Overview

- **App:** vpic-kokkos (LANL, C++ Kokkos-based Particle-In-Cell code), cloned from
  https://github.com/lanl/vpic-kokkos @ master into
  `<WS>/source` where `<WS>=$PROJECT_ROOT/workspaces/vpic_kokkos/20260714_155730`.
- **Build system:** CMake (uses Kokkos as a submodule/dependency + MPI).
- **System:** tuolumne (Cray PE, AMD MI300A APUs — CPU/GPU share memory, no
  explicit device transfer). MPI launcher: `flux run -n <N>`.
  Modules (load in this order): craype-x86-trento, libfabric/match_SHS,
  craype-network-ofi, perftools-base/25.09.0, craype/2.7.35, PrgEnv-cray/8.7.0,
  flux_wrappers/0.1, xpmem/2.6.5, cce/20.0.0, cray-libsci/25.09.0,
  cray-mpich/9.0.1, python/3.13.2.
  `export LD_LIBRARY_PATH="/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:${LD_LIBRARY_PATH}"`
  — the `/usr/lib64` (libdl) entry is REQUIRED before `session_install_dftracer` /
  `session_build_annotated`, or linking dftracer_core / brahma fails with
  `undefined reference: dlopen (disallowed by --no-allow-shlib-undefined)`.
  Set this export in the SAME script/session that runs install/build steps —
  it does not persist across separate Bash tool calls into the MCP tool's own
  process.
- **Kokkos backend decision:** default to **Serial or OpenMP host backend** for
  this pipeline. Do NOT enable Kokkos-HIP / GPU offload unless the
  build-app step confirms the app's own build scripts require it — do not
  assume HIP tracing is needed just because ROCm exists on the system (past
  bug: `bug_hip_tracing_false_positive`). If vpic-kokkos's CMake defaults
  force HIP because MI300A is detected, note that explicitly in the STEP 2
  update and carry the decision forward to annotation (enable HIP tracing in
  dftracer only if the app build step actually links Kokkos-HIP).
- **I/O characterization (for the annotator):** VPIC does its own custom
  binary particle+field dump I/O (raw POSIX read/write via its dump/io
  routines) — it is NOT necessarily HDF5. The annotator step MUST grep the
  actual source (`grep -rn "fopen\|fwrite\|fread\|FileIO\|dump_particles\|dump_fields"`
  or better, use `graph_query` on the app graph) to find the real I/O call
  sites rather than assuming HDF5 APIs.
- **Test decks:** vpic-kokkos ships example input decks (`.cxx` files compiled
  per-deck, e.g. under `deck/` or `sample/`). Use the SMALLEST available deck
  (e.g. a small `harris`/`reconnection` test or the project's own
  `regression`/`sample` deck) for the smoke test; a larger/longer-duration or
  higher-cell-count deck (or the same deck run longer) for the 8-node
  validation.
- **Allocations — UPDATED (original two, <flux-jobid> / <flux-jobid>, EXPIRED
  mid-pipeline; pdebug allocations are short-lived — always re-check state
  with `flux jobs -no "{id} {state} {t_remaining}" <id>` before use, never
  assume an allocation given earlier in the session is still alive):**
  - Three fresh 8-node pdebug allocations were spawned by the user:
    `<flux-jobid>`, `<flux-jobid>`, `<flux-jobid>` (all queued/SCHED as of
    this update — poll `flux jobs -no "{id} {state} {t_remaining}" <id>`
    until one shows `RUN` before using it).
  - `<flux-jobid>` — use for STEPS 5-6 (build-smoke, smoke/best-case trace
    run). Use only 1 node / a handful of ranks from within it via
    `flux proxy <flux-jobid> bash <wrapper>.sh ...` (background, never
    foreground — Bash tool caps at ~10 min and killing the proxy client
    kills the job).
  - `<flux-jobid>` — RESERVE for STEP 8 (8-node optimization/validation run).
  - `<flux-jobid>` — spare, use if either of the above expires before its
    step finishes.
  - Always write a bash wrapper script (module loads + env + the actual run
    command) and invoke it via `flux proxy <JOBID> bash <wrapper>.sh ...`
    with `run_in_background: true`; never inline module loads directly in
    the proxy command.
- **Data placement (MANDATORY, per project Lustre-I/O rule):** `<WS>/dataset`
  is a symlink to `$LUSTRE_ROOT/dftracer-agents/vpic_kokkos/20260714_155730/data`
  on the Tuolumne parallel filesystem (PFS). ALL VPIC application data —
  simulation output, particle/field dumps, restart/checkpoint files, per-deck
  run directories — MUST be written under `<WS>/dataset/<run_name>/` (i.e.
  physically on Lustre), for every stage from the smoke test through the
  8-node validation run. This is separate from dftracer TRACES, which always
  stay under `<WS>/traces/...` or `<WS>/baseline/traces/...` in the session
  workspace (never on Lustre) per `feedback-lustre-io` /
  `feedback-optimization-pipeline-traces`. Every run command (smoke,
  best_case, 8-node) must `cd` into (or pass as VPIC's working/output
  directory) the corresponding `<WS>/dataset/<run_name>/` subdir so
  simulation output lands on Lustre while `DFTRACER_LOG_FILE` still points
  at the workspace traces dir.
- **Canonical paths (baseline run, from `session_get_run_paths`):**
  - `run_dir`: `<WS>/baseline`
  - `source_dir`: `<WS>/baseline/source` (STEP 1 copies/clones original
    source here for the unannotated build; the pristine clone is at
    `<WS>/source`)
  - `traces_raw`: `<WS>/baseline/traces/raw`
  - `traces_compact`: `<WS>/baseline/traces/compact`
  - `scripts_dir`: `<WS>/baseline/scripts`
  - Annotated tree lives under `<WS>/annotated/` (subfolder="annotated" in
    `session_write_file`/`session_read_file`).
  - All logs go to `<WS>/artifacts/<NN>_<step>.log`.
- Each step agent MUST bracket its work with
  `profile_step_begin(step="STEP N: <agent-name>", agent="<agent-name>")` /
  `profile_step_end(...)`, reusing the heading text verbatim, per the
  dftracer-profiling skill. Profile is already bound to this run_id.

## STEP 1: dftracer-session-setup

- Tools first: `session_status`, `session_get_run_paths`, `system_detect`.
- Source already cloned into `<WS>/source` (ref `master`) — do not re-clone.
  Confirm with `session_status` (already shows `step: cloned`).
- Detect build system: confirm CMake (look for top-level `CMakeLists.txt`,
  `arch/` config files typical of VPIC — VPIC historically uses a
  `bin/vpic` config-and-build wrapper script over CMake; check for
  `arch/generic-Debug`/`arch/generic-Release` style configs. Use
  `graph_ensure(run_id=<run_id>)` then `graph_query(run_id=<run_id>,
  question="build configuration cmake kokkos")` to locate the real build
  entry point rather than reading the whole tree.
- Confirm Kokkos dependency mode: bundled submodule vs. externally supplied.
  Run `git -C <WS>/source submodule status` if `.gitmodules` exists.
- Load modules listed in Overview, in order, then export the `LD_LIBRARY_PATH`
  fix (with `/usr/lib64`) in the SAME wrapper script that later steps will
  reuse — write this wrapper once to `<WS>/tmp/env_tuolumne.sh` so every
  downstream step sources it identically (env consistency rule).
- Artifact to report: confirmed build tool (cmake), Kokkos backend decision
  (Serial/OpenMP vs HIP) based on actual CMake defaults inspected, path to
  `<WS>/tmp/env_tuolumne.sh`, and update this plan's STEP 2 section with the
  concrete configure command found.
- Record any system/build quirk into `system-tuolumne` or `workload-vpic-kokkos`
  skill (propose only, per confirmation gate) — do not self-persist.

## STEP 2: dftracer-build-app

- Tools first: `session_configure`, `session_build_install`.
- Use allocation `<flux-jobid>` (1 node) via flux proxy wrapper if any build
  step needs compute-node resources (usually build steps run fine on the
  login/service node used by the MCP tool; only use the allocation if the
  build system requires launching a node-bound compile, e.g. cross-compile
  checks).
- Build ORIGINAL (unannotated) vpic-kokkos into `<WS>/baseline/source`
  (or wherever `session_configure` places it per its own convention — follow
  the paths `session_get_run_paths` returns, do not hand-build).
- Configure flags: CMake with MPI enabled, Kokkos backend = Serial or OpenMP
  (per STEP 1's decision) — e.g.
  `-DKokkos_ENABLE_OPENMP=ON -DKokkos_ENABLE_SERIAL=ON -DKokkos_ENABLE_HIP=OFF`
  unless STEP 1 found the app's own build script forces HIP, in which case
  follow that script verbatim (env consistency rule — never diverge from the
  app's own install/run scripts).
  Bind `CC`/`CXX` to the Cray MPI wrappers (`cc`/`CC` under PrgEnv-cray, which
  wrap `cray-mpich`) — confirm with `which cc CC` inside the module-loaded
  wrapper script, not bare `mpicc`.
- Expected artifact: successful build exit 0, binary path (e.g. `<run_dir
  >/build/bin/vpic` or similar per VPIC's `bin/vpic` deck-compilation
  wrapper — VPIC typically builds a `vpic` compiler-wrapper binary that
  then compiles each input deck `.cxx` into its own per-deck executable;
  confirm this two-stage build pattern during this step and note it for
  STEP 5).
- Log to `<WS>/artifacts/02_build_app.log`.
- Update STEP 4/5 sections of this plan with the confirmed binary/deck build
  pattern once discovered (living-document rule).

## STEP 3: dftracer-build-dftracer

- Tools first: `session_install_dftracer`, `session_install_dftracer_utils`.
- Install into the SAME env/module state as STEP 2 (same modules, same
  `LD_LIBRARY_PATH` wrapper from `<WS>/tmp/env_tuolumne.sh`).
- Pass MPI explicitly via env vars (per software-mpi skill): point dftracer's
  build at `cray-mpich/9.0.1` include/lib paths, bind `CC`/`CXX` identically
  to STEP 2's compiler wrappers so linking is ABI-compatible.
- HIP tracing: leave OFF unless STEP 2 confirmed the app actually links
  Kokkos-HIP (do not enable dftracer HIP tracing from ROCm presence alone —
  known false-positive bug).
- HDF5: only wire HDF5 support into dftracer if STEP-1/2 source inspection
  shows vpic-kokkos actually uses HDF5 for I/O (unlikely — VPIC has its own
  custom binary dump format); otherwise skip HDF5 entirely for this app.
- Verify: `python -c "import dftracer.dftracer"` succeeds AND a nonzero exit
  from the tool install; report `features_enabled` list.
- Log to `<WS>/artifacts/03_build_dftracer.log`.
- Expected artifact: dftracer install path, features_enabled, confirmation
  that the same `LD_LIBRARY_PATH` fix (`/usr/lib64` for `libdl`) was applied
  BEFORE this call (else dlopen link failure per known system-tuolumne bug).

## STEP 4: dftracer-annotator

- Tools first: `graph_ensure(run_id=<run_id>)` then `graph_query(run_id=...,
  question="I/O read write dump particles fields checkpoint")` to locate
  actual I/O call sites — do NOT grep/Read the whole tree.
- Scope: C++ (Kokkos). Annotate at minimum:
  - top-level simulation driver / main loop (compute region)
  - VPIC's custom dump/IO routines (particle dump, field dump, restart
    checkpoint/restore) — these are the I/O hot paths, likely raw
    POSIX/`FileIO` wrapper class calls, not HDF5.
  - Any MPI collective/halo-exchange functions (comm region) if identifiable
    via the graph (`boundary`, `halo`, `ghost`, `Allreduce` symbols).
- Exclude tight per-particle inner Kokkos parallel_for lambda bodies from
  per-call annotation (hot-loop — would blow up trace volume); annotate at
  the enclosing function/kernel-launch level instead.
- Use `DFTRACER_C_FUNCTION_START` / `DFTRACER_C_FUNCTION_END` macros (this is
  C++, use the C macros — confirm the C++ variant exists in the dftracer
  skill; if not, wrap manually with matching START/END pairs, respecting
  RAII/early-return exit paths — VPIC's I/O routines may have multiple
  return points, each needs its own END or use a scope-guard pattern).
- Write annotated files under `<WS>/annotated/` preserving the source tree
  layout via `session_write_file(subfolder="annotated")`.
- **Decision point requiring human confirmation:** report the file count and
  function list before proceeding to STEP 5 if it exceeds ~15 files or touches
  files outside the dump/IO + main-loop scope described above.
- Validate: grep-verify every START has a matching END (past bug: annotator
  agents have fabricated "success" claims — always verify writes landed on
  disk with grep before reporting done).
- Log to `<WS>/artifacts/04_annotate.log`.

- **STEP 4 result (annotation complete):** 7 files annotated under `<WS>/annotated/source/`:
  `src/vpic/dump.cc` (13 funcs: dump_energies/species/materials/grid/fields/hydro/particles,
  create_field_list/hydro_list, print_hashed_comment, global_header, field_dump, hydro_dump — all comp="io"
  except the two list-builder helpers comp="cpu"), `src/vpic/vpic.cc` (8 funcs incl. checkpt_kokkos/
  restore_kokkos = comp="io", rest comp="cpu"), `src/vpic/advance.cc` (1 func: advance() = the main
  per-timestep simulation loop, comp="cpu"), `src/util/checkpt/checkpt.cc` (15 funcs, comp="cpu",
  serialization layer), `src/util/checkpt/checkpt_io.cc` (5 funcs: checkpt_open_rdonly/wronly/close/read/write,
  comp="io" — the actual POSIX I/O primitives), `src/boundary/boundary_p.cc` (1 func: boundary_p_kokkos =
  MPI halo/particle-boundary exchange, comp="comm"), `deck/main.cc` (entry point: DFTRACER_CPP_INIT +
  DFTRACER_CPP_REGION_START("main") at body start, DFTRACER_CPP_REGION_END+DFTRACER_CPP_FINI before the
  single return; also annotates the checkpt() helper comp="io"). Total 44 functions annotated.
  extra_include_dirs needed for any future syntax-check of these files:
  `<WS>/dftracer_build/include`, `<WS>/venv/lib/python3.13/site-packages/dftracer/include`, and for
  Kokkos-including files also `<WS>/install/include/kokkos` (NOTE: clang_syntax_check hardcodes
  -std=c++14 which breaks on Kokkos C++17 headers — this is a tool/toolchain mismatch, not an annotation bug;
  the real CMake build uses the correct standard). All files pass clang_lint_annotations (0 issues) and
  clang_extract_functions structural re-check (function/line counts unchanged before/after annotation).

## STEP 5: dftracer-build-smoke

- Tools first: `session_build_annotated`, `session_run_smoke_test`.
- Build the annotated tree from `<WS>/annotated/` using the SAME configure
  flags discovered in STEP 2 (same Kokkos backend, same CC/CXX, same
  LD_LIBRARY_PATH wrapper).
- Smoke test: use allocation `<flux-jobid>`, 1 node, few ranks (e.g.
  `flux proxy <flux-jobid> bash <wrapper>.sh flux run -N1 -n4 <vpic-binary
  or per-deck-compiled binary> <smallest test deck>`, run_in_background:true).
  Use VPIC's smallest sample/regression deck (confirm exact deck path from
  STEP 2's build-pattern discovery). Run with working/output directory set
  to `<WS>/dataset/smoke/` (create it, on Lustre via the `dataset` symlink)
  so VPIC's own dump/checkpoint files land on the PFS, not the workspace.
- **MANDATORY (Pipeline Policy rule 12):** call
  `session_service_start(run_id="vpic_kokkos/20260714_155730")` immediately
  before launching the smoke run and `session_service_stop(run_id=...)`
  immediately after it completes — one `dftracer_service` daemon per node,
  pinned to one core, for node-level counters (separate from per-rank app
  traces at `<WS>/traces/<run_id>.*` vs service traces at
  `<WS>/traces/service_<hostname>.*`).
- `DFTRACER_INIT` mode: init at process start (PID-based log naming) since
  VPIC is MPI-SPMD; `DFTRACER_LOG_FILE` must point into
  `<WS>/baseline/traces/raw/smoke-` or a dedicated `<WS>/traces/smoke/`
  subdir — NEVER onto Lustre for trace files, and NEVER set
  `DFTRACER_DISABLE_IO`.
- Expected artifact: exit 0, at least one non-empty `.pfw` (or `.pfw.gz`)
  trace file per rank; verify with `ls -la` + `zcat | head` if gzipped.
- Log to `<WS>/artifacts/05_build_smoke.log`.

## STEP 6: dftracer-tracer

- Tools first: `session_run_with_dftracer`, `session_split_traces`.
- Best-case trace run: still on allocation `<flux-jobid>`, slightly larger
  than the pure smoke test if time allows (e.g. same 1 node, more ranks, or
  a few more timesteps of the small deck) to get a representative but cheap
  trace before committing to the expensive 8-node run.
- `data_dir` = `<WS>/traces/best_case/` (session workspace, never Lustre for
  traces themselves). VPIC's own simulation output/checkpoints for this run
  go to `<WS>/dataset/best_case/` (on Lustre via the `dataset` symlink, per
  Pipeline Policy rule 11) — set that as the app's working/output directory.
- `env_extra`: the `<WS>/tmp/env_tuolumne.sh` wrapper env plus
  `DFTRACER_LOG_FILE=<WS>/traces/best_case/vpic-`.
- `run_name`: `best_case`.
- **MANDATORY (Pipeline Policy rule 12):** bracket the launch with
  `session_service_start(run_id=...)` before / `session_service_stop(run_id=...)`
  after — one `dftracer_service` daemon per node, pinned to one core.
- After run completes, call `session_split_traces` to produce per-rank
  split traces; report output dir + file count.
- Log to `<WS>/artifacts/06_tracer.log`.

## STEP 7: dftracer-analyzer then dftracer-diagnoser

- Tools first: `session_analyze_traces`, then diagnosis tool/graph query.
- Preset: use the generic POSIX/compute preset (not the DLIO/AI-ML preset —
  this is an MPI HPC simulation code, not a data-loader). Confirm exact
  preset name via `list_presets()`.
- Views to inspect: I/O time breakdown (dump/checkpoint routines), compute
  time (Kokkos parallel_for regions if captured), MPI/comm time (halo
  exchange, collectives), and memory footprint if `DFTRACER_ENABLE_MEM`-style
  markers were added.
- Checkpoint dir: `<WS>/traces/best_case/` (from STEP 6).
- diagnoser step ranks candidate bottlenecks (I/O-bound dump path stalling
  compute? MPI collective imbalance? memory pressure from MI300A shared
  memory oversubscription?) — this determines what STEP 8's optimizer
  should target first.
- Log to `<WS>/artifacts/07_analyze.log` and `<WS>/artifacts/07_diagnose.log`.
- This stage may start on the best_case trace WHILE STEP 8 preparation
  (allocation time-check, wrapper scripts) proceeds in parallel — do not
  block STEP 8 setup on STEP 7 finishing analysis text, only on STEP 7
  producing the bottleneck ranking STEP 8 needs to target.

## STEP 8: dftracer-optimizer (8-node validation run)

- **STEP 7 result (diagnosed bottleneck, from best_case 16-rank/1-node trace):**
  VPIC is **MPI-communication-bound**, NOT I/O-bound and NOT (raw-kernel)
  compute-bound. Per-rank breakdown (2 ranks cross-checked, consistent):
  `main`=3.51s wall (single outer span) -> `advance()` (per-timestep loop)
  =3.41s (97% of wall time, 1001 calls) -> within `advance()`, the UNION of
  `dump_energies` (nested MPI_Allreduce-dominated global energy reduction,
  1.68s, 1001 calls, 99.7% of which IS `MPI_Allreduce` -- 1.65s/3008 calls)
  and `boundary_p_kokkos` (MPI halo/particle-boundary exchange, 1.12s, 3000
  calls, itself containing most of the 48,084 `MPI_Wait` calls, 0.91s) =
  **2.80s = 82% of advance()'s wall time**. Pure Kokkos compute (advance minus
  that union) is only ~0.61s (18% of the loop). Raw POSIX I/O is negligible:
  19ms/rank (1194 ops: `access`, plus `STDIO fopen/fgets/fclose` ~19ms) -- under
  0.2% of total time. **`dump_energies` was annotated `comp="io"` but is
  functionally a communication routine** (global MPI_Allreduce reduction for
  energy diagnostics), not disk I/O -- do not let its `io` label mislead the
  optimizer.
  - Tool caveat: `mcp__dftracer__analyze`/`diagnose` on this trace under-counted
    (reported 527,206 events / 4 processes vs. the known 2,111,806 events / 16
    ranks -- consistent across 2 reruns, so not the previously-documented
    non-determinism, but still wrong) and its POSIX metrics were scored
    "critical" purely from a percentile/fraction artifact on 1-4 absolute op
    counts (e.g. `posix_close_count_sum=1.0`) -- these are NOT real bottlenecks;
    ignore them. The ranking above comes from manual per-event `dur` aggregation
    directly on the `.pfw.gz` traces (2 ranks: `vpic--0049d4b15cf040ff-app` and
    `vpic--040fb0cc3da38e5f-app`), cross-checked with nesting/containment
    analysis (`ts`/`dur` interval containment) to avoid double-counting
    overlapping parent/child spans.
  - **Metric objective for STEP 8:** minimize MPI time inside `advance()` --
    specifically reduce `MPI_Allreduce` cost in `dump_energies` (e.g. lower
    energy-diagnostic output cadence is a "do less" non-starter per policy;
    instead look at whether the reduction can overlap with compute, use a
    non-blocking `MPI_Iallreduce`, or whether Allreduce cost is inflated by an
    unnecessary sync point) and `boundary_p_kokkos`/`MPI_Wait` imbalance (check
    for load imbalance across the 16 local ranks -- this is single-node MPI, so
    high Allreduce/Wait cost points to synchronization/imbalance overhead, not
    network latency). Do NOT target POSIX/dump-file I/O tuning (ROMIO,
    striping, buffering) as the primary lever -- the analysis shows it is not
    the constraint for this workload/config.

- Tools first: `session_optimization_iteration` (or equivalent optimizer MCP
  tool), `flux jobs -no "{id} {state} {t_remaining}" <flux-jobid>` to check
  remaining time BEFORE starting.
- Use allocation `<flux-jobid>` (8 nodes: tuolumne[1021-1023,1025-1028,1032]).
  Launch via a bash wrapper + `flux proxy <flux-jobid> bash <wrapper>.sh ...`
  with `run_in_background: true` — never foreground.
- Scale up to a larger/longer-duration input deck appropriate for 8 nodes
  (per Overview's deck note) — fix the same deck/duration/iteration-count
  across baseline and every optimization variant tested at this scale (equal
  work rule). Target at least ~10 minutes of run time so I/O/comm/memory
  effects are resolvable above noise; take at least one baseline replicate
  and one replicate of the best variant.
- Metric objective: driven by STEP 7's diagnosed top bottleneck (e.g. reduce
  dump/checkpoint I/O time, reduce halo-exchange comm time, or reduce
  per-rank memory pressure) — state the objective explicitly before
  iterating.
- Termination: stop after the diagnosed bottleneck's metric plateaus across
  2 consecutive proposal iterations, or after 4 iterations, whichever first;
  do not treat reduced work (fewer timesteps, larger checkpoint interval,
  smaller deck) as a valid "speedup" — check against fixed total
  particle-steps / bytes dumped.
- Traces for each iteration go to `<WS>/traces/opt_iterN/`, never Lustre.
  VPIC's own simulation output/checkpoints for each variant go to
  `<WS>/dataset/opt_iterN/` (Lustre, via the `dataset` symlink, per Pipeline
  Policy rule 11) as that variant's app working/output directory.
- **MANDATORY (Pipeline Policy rule 12):** for EVERY iteration's launch
  (baseline replicate + each variant), bracket with
  `session_service_start(run_id=...)` / `session_service_stop(run_id=...)` —
  one `dftracer_service` daemon per node (all 8 nodes), pinned to one core
  each, not just for the final winning configuration.
- Expected artifact: comparison table baseline vs best-variant with noise
  band, and the winning configuration's exact flags/params.
- Log to `<WS>/artifacts/08_optimize.log`.

## STEP 9: dftracer-privacy-guard (MANDATORY final step)

- Tools first: `privacy_scan(run_id=<run_id>)`.
- Run AFTER all self-learning proposals have been confirmed by the user and
  persisted (skills/agents/memory), scanning the session workspace and any
  touched skill/memory files for usernames, absolute user paths, flux job
  ids, session UUIDs, node hostnames.
- Must report `clean` before the session is considered done; if not clean,
  redact and re-scan.
- Also call `profile_report()` a few seconds after STEP 8 ends (main-thread
  responsibility, not this agent's) to finalize
  `<WS>/performance/performance_report.md`.

## DISPATCH ORDER

dftracer-session-setup, dftracer-build-app, dftracer-build-dftracer, dftracer-annotator, dftracer-build-smoke, dftracer-tracer, dftracer-analyzer, dftracer-diagnoser, dftracer-optimizer, dftracer-privacy-guard
