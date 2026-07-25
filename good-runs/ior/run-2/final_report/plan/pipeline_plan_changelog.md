
## 2026-07-24 STEP 2 (dftracer-build-smoke)
- Root cause found and fixed: annotated/source/configure.ac still had the
  original AC_PREREQ([2.71]) (system autoconf is 2.69); the earlier
  session-configure patch to 2.69 was applied only to the plain source/ tree,
  not carried into annotated/ by the annotation pass. Fixed in-tree in
  annotated/source/configure.ac and regenerated via
  `autoreconf -fi -I config` (never copy configure/Makefile.in artifacts
  from a sibling tree - they must be regenerated for the annotated tree's
  own source files).
- IOR's --with-hdf5 autotools flag takes yes/no, not a path; HDF5
  include/lib must be supplied via CPPFLAGS/LDFLAGS pointing at
  install_hdf5. Also required LIBS="-ldftracer_core" explicitly (dftracer
  ships no .pc file), otherwise link fails on initialize_main/finalize/
  update_metadata_string undefined symbols (DFTRACER_C_* macro expansions).
- scripts/env.sh (session-detect) OVERWRITES LD_LIBRARY_PATH after `module
  load` instead of appending, silently dropping cray-mpich's
  libmpi_cray.so.12 and cray-pmi's libpmi.so.0 - smoke run failed at
  runtime with "error while loading shared libraries" until
  LD_LIBRARY_PATH was appended-to (session-local paths first) rather than
  replaced.
- Working, idempotent build recipe captured at
  annotated/scripts/build.sh (sources scripts/env.sh, appends
  session-local LD_LIBRARY_PATH, applies the AC_PREREQ fix once,
  autoreconf, configure, make, make install).
- Smoke test (single process): HDF5 backend (write 51.6 MiB/s / read 765
  MiB/s at -b1m -t1m -s1) and MPIIO backend both ran cleanly.
  DFTRACER_LOG_FILE=baseline/traces/raw/smoke_hdf5 produced a non-empty
  gzip .pfw with categories {MPI, POSIX, HDF5, MPIIO, STDIO, C_APP,
  dftracer} - 810 events for the HDF5 run.
- Annotation coverage per session_annotation_report: 360/558 functions
  (64.5%) - gaps are almost entirely unused backends (CEPHFS, DAOS, S3,
  etc.) not on the HDF5/POSIX/MPIIO smoke path; not a blocker for this
  session's scope.

## 2026-07-24 — STEP 5c dftracer-optimizer-communication
- Bounded the communication dimension from the baseline compact traces: in-region MPI collective time = MPI_Allreduce 1.192s + MPI_Barrier 0.429s + MPI_Bcast 0.008s = 1.63s vs C_APP 376.3s => 0.43% hard ceiling, BELOW the 2.8% write / 1.5% read baseline noise band. MPI_Init (8.57s) is startup, outside the timed region.
- Confirmed structurally from the trace that the HDF5 -F path uses MPI_File_write_at / MPI_File_read_at (INDEPENDENT) and ZERO MPI_File_*_all, so ROMIO collective buffering / cb_nodes / CRAY_CB_NODES_MULTIPLIER are inert by construction (empirical confirmation of the prior sessions inference).
- Measured 2 env-var-only variants at 512 ranks/8 nodes UNTRACED with interleaved concurrent controls: MPICH_OFI_NIC_POLICY=NUMA (NO_CHANGE) and MPICH_SHARED_MEM_COLL_OPT=1 (NO_CHANGE, order-alternated design). All ranges overlap the pooled control.
- IMPORTANT for STEP 6: UNTRACED 512-rank write bandwidth is ~29.2 GB/s (control, 6 reps [28638, 29409] MiB/s) and read ~17.0 GB/s, vs the STEP 3 TRACED baseline of 19.7 / 13.6 GiB/s. Tracing overhead is ~33% on write. Any variant-vs-baseline delta in the final report MUST compare like-for-like (traced vs traced, or untraced vs untraced) — do not compare an untraced variant against the traced STEP 3 baseline.
- No source changes made (annotated/ owned by the I/O sibling this pass). Artifacts: artifacts/05c_comm_ab.log, artifacts/05c_comm_ab2.log; scripts tmp/comm_ab.sh, tmp/comm_ab2.sh; run record opt_comm/.
