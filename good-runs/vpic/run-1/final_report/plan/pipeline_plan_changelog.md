# Pipeline Plan Changelog
## 2026-07-14 STEP 4 dftracer-annotator

- Annotated 7 files / 44 functions (dump.cc, vpic.cc, advance.cc, checkpt.cc, checkpt_io.cc,
  boundary_p.cc, deck/main.cc) using DFTRACER_CPP_FUNCTION()/UPDATE macros (RAII, no END needed)
  plus DFTRACER_CPP_INIT/REGION_START/REGION_END/FINI in deck/main.cc's main().
- clang_annotate_file threw "list index out of range" on 5/7 files; worked around via manual
  clang_insert_line calls driven by clang_extract_functions output. On the one file it did succeed on
  (checkpt.cc) its internal clang_add_braces pass corrupted an if/else into two disconnected compound
  blocks (real compile error) — manually repaired.
- Verified coverage via grep (FUNCTION()==UPDATE(comp) counts, single include, INIT/REGION placement)
  since session_annotation_report's regex-based coverage tool reported 0% (looks like a report-tool/
  macro-pattern mismatch, not an actual gap).

## [2026-07-14 11:30 PDT] STEP 6 Complete: Best-Case Trace Run

**Agent:** dftracer-tracer

**Completed Actions:**
- Launched best_case trace run: 16 MPI ranks on 1 node, Weibel.cxx deck
- DFTracer mode: FUNCTION (source-level annotation)
- Allocation: <flux-jobid> (~46 min remaining, sufficient)
- dftracer_service daemon: Started/stopped successfully
- VPIC binary: `<WS>/dataset/smoke_weibel/Weibel.Linux` (pre-compiled, reused)
- Output directory: `<WS>/dataset/best_case/` (Lustre via dataset symlink)

**Trace Results:**
- Raw trace files: 17 (16 rank traces + 1 service daemon trace)
- Total events: 2,111,806
- Total size: 21 MB
- File format: vpic-*.pfw.gz (gzipped Chrome trace events)
- Status: All non-empty, successfully indexed

**Trace Indexing:**
- RocksDB index created in `<WS>/traces/best_case/.dftindex/` (~1.1 MB)
- Index enables efficient event querying for STEP 7 analysis

**Next Step (STEP 7):**
- Trace checkpoint dir: `<WS>/traces/best_case/`
- Ready for POSIX/compute preset analysis
- Expect to identify I/O, compute, and communication bottlenecks

**Notes:**
- Wrapper scripts: `<WS>/tmp/best_case_run.sh`, `<WS>/tmp/best_case_full.sh`
- Run log: `<WS>/artifacts/06_tracer_run.log`
- Summary: `<WS>/artifacts/06_tracer_summary.txt`

## 2026-07-14 (STEP 7: dftracer-analyzer / dftracer-diagnoser)
- Ran mcp__dftracer__event_count, mcp__dftracer__analyze (posix preset,
  cluster_n_workers=32, checkpoint enabled, 2 reruns), and
  mcp__dftracer__diagnose on `<WS>/traces/best_case/`. analyze()/diagnose()
  under-counted (527,206 events / 4 procs vs. actual 2,111,806 events / 16
  ranks) and diagnose()'s POSIX "critical" findings were percentile artifacts
  on 1-4 absolute op counts, not real bottlenecks.
- Cross-checked manually: per-event `dur` aggregation + ts/dur containment
  analysis on 2 rank .pfw.gz files shows VPIC best_case is MPI-communication
  bound (82% of the advance() timestep loop is inside MPI_Allreduce
  (dump_energies) + boundary_p_kokkos halo exchange / MPI_Wait), not
  I/O-bound (raw POSIX <0.2% of time) and not raw-compute-bound (~18% of the
  loop). Updated STEP 8's section with this diagnosis and metric objective.

## 2026-07-14 STEP 8a dftracer-optimizer-communication
- Root-caused dump_energies comm cost: 3 SEPARATE small blocking mp_allsum_d/step (1x6-dbl energy_f + Nsp x1-dbl energy_p; Weibel Nsp=2 -> ~3008 Allreduce). Small-msg latency/sync-bound.
- Produced 5 cited proposals (opt_proposal_table, 5/5 accepted). NOT MEASURED this pass: aggregation+Iallreduce are libvpic-library changes needing a rebuild of the annotated tree STEP 9 depends on; not worth risking a dirty tree in a shared ~31-min window. Deferred to STEP 9.
- STEP 9 TODO (in priority order): (1) Apply PATCH #1 message-aggregation (3 Allreduce/step->1; -40..60% expected) in energy_f.cc/energy_p.cc/dump.cc, incremental libvpic relink (~2-5min, Kokkos prebuilt), validate energies-file identity + trace dur, 5 replicates + comparator. (2) Stack PATCH #2 MPI_Iallreduce overlap. (3) L2 cray-mpich MPICH_ALLREDUCE_* env sweep (subsumed by #1). See artifacts/08_optimize_comm.log for exact patches+citations.
