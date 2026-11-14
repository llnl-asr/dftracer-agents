---
name: dftracer_smoke
description: Run a single-process smoke test of the (annotated) application with session_run_smoke_test - MPI launchers and their rank flags stripped, the module preamble from the app's own scripts prepended, flux-proxy commands wrapped in a script - and validate it with dftracer_smoke_check (passed, single process, no /tmp output, no dftracer failure strings, non-empty traces). Load at pipeline stage 3 (build + smoke), or on "return code 127", an empty or 580-byte trace, "unable to create log file ... errno=2", "Code 2001", "DFTracer was enabled but not available", a Fortran binary with empty FUNCTION-mode traces, or a smoke test someone wants to run with several ranks.
---

# dftracer_smoke

Converted from dftracer-agents' `session_run_smoke_test`, the `dftracer-smoke-test` skill, the smoke half of the
`dftracer-build-smoke` agent (.yaml and .md) and the run entries of `dftracer-pitfalls`.

## Rules
- ALWAYS a single process, never MPI or any parallelism. `session_run_smoke_test` strips `mpirun`, `mpiexec`, `srun`,
  `jsrun`, `aprun`, `flux run` and their `-np/-n/--ntasks` flags. If the app calls MPI_Init unconditionally, pass
  `mpirun -np 1 <binary> <args>` so the tool reduces it to one rank - never several ranks. No `make -j N`, no
  `OMP_NUM_THREADS > 1` or `GOMP_SPINCOUNT` in smoke env vars.
- The minimal happy path: one input file, one iteration, smallest data size, no checksum/verification flags.
- HARD RULE - smoke data and trace output go to the system-detected parallel file system (from system_detect; on
  Tuolumne Lustre `/p/lustre5/$USER`), never `/tmp` or the shared home. Traces are routed into the workspace by the
  tools; the app's DATA files (checkpoints, plotfiles, IOR output, datasets) must be pointed at the PFS by the command
  or parameter file. If the PFS is unavailable, stop and report it before running.
  Good: `mkdir -p $LUSTRE_ROOT/$USER/workspaces/ior && ./src/ior -a POSIX -b 1m -t 1m -s 1 -F -C -o
  $LUSTRE_ROOT/$USER/workspaces/ior/ior_smoke_test`. Bad: `mpirun -np 4 ./src/ior ...`, or `-o /tmp/ior_smoke_test`.
- Order (stage 3): precondition `validate_annotations` passed -> `session_build_annotated` (dftracer_build) ->
  `session_service_start` -> `session_run_smoke_test(run_id, command, subfolder)` -> `session_service_stop` ->
  `session_annotation_report` -> `dftracer_smoke_check(run_id, trace_dir=...)`. Bracket the smoke launch with the
  node-counter daemon (pipeline rule 12) even for a cheap single-process test; if those tools are unavailable, run the
  smoke alone and flag the missing bracketing in the report.
- DFTRACER_INIT: FUNCTION first for annotated binaries; fall back only as below; never `DFTRACER_INIT=0`. If the
  annotated source contains explicit `DFTRACER_C_INIT()`/`DFTRACER_CPP_INIT()` (`grep -r
  "DFTRACER_C_INIT\|DFTRACER_CPP_INIT" annotated/`), never set `DFTRACER_INIT=1` (double init -> empty or corrupted
  trace): pass `env_extra='{"DFTRACER_INIT":"FUNCTION"}'`.
- Use the FULL ABSOLUTE path of the binary (find it outside `source/` first); a relative `./ior` gives return code 127.
- Failure triage: a failure on dftracer symbols or an annotated function -> annotation issue, escalate to the annotator;
  a non-annotation failure -> report and ask before continuing.
- Return: build status, smoke status + runtime, annotation report summary.

## Constraints
- Never edit source to make the smoke pass. Never run the smoke at scale; never compare a smoke result with a
  production run. Logs go to `<WS>/artifacts/`.

## Corner cases
- Fortran programs (Flash-X) have no C main(): before FUNCTION mode verify a C main or a constructor/destructor
  wrapper is linked; if FUNCTION traces are empty while the binary runs, the Fortran linker did not fire the
  constructors - pivot to PRELOAD: `DFTRACER_INIT=PRELOAD`, `DFTRACER_DATA_DIR=all`,
  `LD_PRELOAD=<path>/libdftracer_core.so.<version>`, and re-run.
- Empty/partial trace with FUNCTION-compiled annotations is almost always DFTRACER_DATA_DIR / DFTRACER_LOG_FILE, NOT a
  reason to switch to PRELOAD/HYBRID: set DFTRACER_DATA_DIR=all, make LOG_FILE an absolute path into an EXISTING
  per-run raw dir (a missing dir logs `unable to create log file ... errno=2` and leaves a 0-byte file).
- Vendor-specific functions (gpfs_*, beegfs_*, lustre_*) annotated but absent from the trace are EXPECTED: they are
  guarded by `#ifdef HAVE_GPFS_FCNTL_H` etc.; report "annotated, not traced (filesystem not available)".
- `session_run_smoke_test` on an unknown run_id runs the command and then raises saving the state (no workspace):
  always pass the run_id session_create returned.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
For each failed check of `dftracer_smoke_check`:
- `smoke_recorded` -> run `session_run_smoke_test` (bracketed by session_service_start/stop).
- `smoke_passed` -> read the output: dftracer symbol / annotated function -> annotator; rc 127 -> absolute binary
  path; otherwise rerun the PRISTINE binary to attribute the failure and ask the user.
- `single_process` -> remove ranks: `mpirun -np 1 <bin>` or the bare binary.
- `no_tmp_output` -> point the app's output at the PFS directory.
- `no_dftracer_errors` -> apply the fix named in the check's `fix` (log dir, DATA_DIR=all, import/GLIBCXX, MPI runtime,
  shared library path, ROCm env).
- `traces_written` -> DATA_DIR/LOG_FILE first; then the Fortran PRELOAD pivot.
Then predict (session_run_smoke_test) and validate again; the same failure twice stops for the user.

## Self-heal
- If the tool mangles a command (launcher stripping, flux-proxy wrapper, module preamble), fix it here
  (capability_update) and restart the server; do not hand-run the smoke as a workaround.

## Self-learn
- What is explored here becomes a capability: an app's minimal smoke command becomes part of `workload_<app>` (its
  WORKED.md), a site's PFS path or launcher quirk `system_<machine>`. See capability_builder.

## What works
- IOR: `session_run_smoke_test(run_id, command="<WS>/install_ann/bin/ior -a POSIX -b 1m -t 1m -s 1 -F -C -o
  $LUSTRE_ROOT/$USER/workspaces/ior/ior_smoke_test", subfolder="build_ann")`, bracketed by session_service_start/stop.
- Annotated C/C++ binaries: `env_extra='{"DFTRACER_INIT":"FUNCTION"}'` when the source has explicit INIT calls.
- Flash-X (Fortran): PRELOAD pivot with DATA_DIR=all when FUNCTION traces stay empty.
