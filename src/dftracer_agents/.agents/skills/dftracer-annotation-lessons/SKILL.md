---
name: dftracer-annotation-lessons
description: >
  Lessons learned from dftracer annotation sessions — real errors, root causes,
  and exact fixes. Loaded by each per-file annotation subagent at startup.
  Updated by the pipeline recipe after every session (Step 8).
---

## Related Skills

Workload-specific lessons and pitfalls live in dedicated skills — load these when working on the corresponding application:

- **[[workload-ior]]** — IOR build quirks, annotation pitfalls, ROMIO/VAST tuning, smoke test
- **[[workload-h5bench]]** — H5Bench build, CMake quirks, assert/else-if brace insertion, INI config

Software-specific optimization strategies:

- **[[software-mpi]]** — MPI-IO, ROMIO hints, Flux env propagation, Cray MPICH
- **[[software-hdf5]]** — HDF5 version, chunk/cache tuning, Cray chid_t, dftracer HDF5 support
- **[[software-posix]]** — POSIX readahead, lustre striping, OS tuning, ops_slope bottlenecks

When appending new session lessons below, also update the workload or software skill file that matches the `app:` field and `tags:` — see Step 9 of [[dftracer-pipeline]].

---

## How to use this file

Read this before annotating any file. For each lesson:
  1. Check if the `context` matches what you are about to do
  2. If so, apply the `fix` proactively — do not repeat the mistake

## How to add new entries

The pipeline recipe (Step 8) appends new entries after each session.
Entries follow this format:

```
---
date: YYYY-MM-DD
app: <git url>
context: <one-line description of what was being attempted>
error: |
  <exact error message or key excerpt>
root_cause: <why it happened>
fix: |
  <exact steps or rule that resolved it>
tags: [<language>, annotation, <error-keyword>]
---
```

Do not delete old entries. Entries accumulate as institutional memory.

---

## Standing rules (always apply, every session)

These are not lessons from failures — they are invariants that must hold:

R1  Read the lessons file before annotating any file (you are doing that now).

R2  Write the COMPLETE file when calling session_write_file. Never a partial.
    Verify: written line count > original line count.

R3  Run coverage verification after every file before moving to the next.
    START/decorator count must equal comp= count.

R4  Never annotate a forward declaration (C/C++: a line ending with ";").
    For any function name found twice, annotate only the definition (has body).

R5  Never annotate a header file (.h / .hpp).
    Put #include <dftracer/dftracer.h> in .c / .cpp files only.

R6  Lifecycle functions (*_init, *_final, *_initialize, *_finalize) are always
    annotated regardless of body length — never apply Rule 0 skip to them.

R7  Vendor filesystem functions (gpfs_*, beegfs_*, lustre_*, hdfs_*, ceph_*,
    daos_*) are always annotated as comp="io".

R8  If annotated code contains explicit DFTRACER_C_INIT() / DFTRACER_CPP_INIT()
    / DFTracer.initialize_log() calls, the environment must have DFTRACER_INIT=0
    when running the binary. Setting DFTRACER_INIT=1 with explicit INIT calls
    produces an empty trace file with no events.

R9  Smoke tests and trace collection runs MUST move more than 50% of each
    node's physical memory to/from the filesystem to avoid OS page-cache
    effects that make I/O look faster than it really is.

    Before setting DIM_* / block size / particle count, query node memory:
      flux run -N 1 -n 1 grep MemTotal /proc/meminfo   # on Flux/Tuolumne
      cat /proc/meminfo | grep MemTotal                  # inside a container

    Then size the dataset so that:
      total_data_written > 0.5 × MemTotal × num_nodes

    Reference values (Tuolumne, AMD MI300A APU nodes):
      MemTotal per node : ~502 GiB
      2-node threshold  : >502 GiB total  (>251 GiB per node)
      4-node threshold  : >1004 GiB total (>251 GiB per node)

    For h5bench_write with 192 ranks across 2 nodes:
      DIM_1=33554432 (32M float32) × 192 ranks × 4B × 4 timesteps = 768 GiB  ✓
      DIM_1=16777216 (16M float32) × 192 ranks × 4B × 4 timesteps = 384 GiB  ✗ (below threshold)

R10 When running multiple benchmark workloads in the same session, keep
    traces and logs SEPARATE per workload and analyze + optimize each
    independently. Do NOT merge traces across workloads into a single
    analysis — different workloads have different access patterns and
    different bottlenecks.

    Correct layout:
      traces/<workload_name>/           ← one directory per workload
      analysis/<workload_name>/         ← separate analysis output per workload

    Correct workflow:
      1. Collect traces per workload into separate subdirectories
         (use DFTRACER_LOG_FILE=$TRACES/<name>/<name> so all 192 rank files
         land in a per-workload folder, not a flat shared directory)
      2. Split each workload's traces independently:
           dftracer_split --directory traces/<name>/ --output traces_split/<name>/
      3. Run dfanalyzer + diagnose per workload independently
      4. Run the optimization loop independently per workload
      5. Parallelize: multiple workload analysis/optimization loops can run
         concurrently (they share no state between workloads)

    Why: Mixing all 192×N trace files into one analysis makes bottleneck
    scores meaningless — write latency from h5bench_write will drown out
    metadata patterns from h5bench_read and vice versa.

    Apply this check to ALL workloads: write, read, append, overwrite,
    write_unlimited, write_normal_dist, hdf5_iotest, IOR, and any future app.

R11 Before applying any optimization at production scale, first validate it
    with a smoke run, then scale up gradually until you find the smallest
    configuration that fails. This isolates whether the failure is from
    the optimization itself or from a scale/data-size interaction.

    Correct debugging sequence for an optimization that may cause failures:
      1. Smoke: 1 node, 4 ranks, tiny DIM (e.g. DIM_1=1M, TIMESTEPS=2)
         → test each optimization in isolation (L1 only, L2 only, L3 only,
           then combinations)
      2. Mid: 1 node, all cores (96 ranks on Tuolumne), same small DIM
         → same isolation tests
      3. Full nodes, small data: 2 nodes × 192 ranks, still small DIM
         → same isolation tests
      4. Full nodes, production data: gradually increase DIM until fail/pass
         confirmed

    Stop at the first scale/DIM where a failure appears — that is the
    minimal reproducer. Record it in the workload or software skill.

    Why: Full-scale runs (192 ranks × 4 timesteps × large DIM) take 10–15
    minutes each. Smoke runs take under 30 seconds. Finding the culprit at
    small scale saves hours of iteration time.

    Apply to: every new optimization hint, config change, or wrapper before
    deploying it in the full optimization loop.

    Tuolumne smoke test template:
      # 4-rank quick check
      flux proxy $JOB flux run -N 1 -n 4 --env LD_LIBRARY_PATH=$LDPATH \
        bash $WS/tmp/wrapper.sh $BIN $WS/tmp/smoke_small.cfg $OUTDIR/out.h5

      # scale to 96 ranks, then 2×96 before running full 4-ts production run

---


## Session logs (appended by pipeline Step 8)

The accumulating dated lesson entries live in a separate sibling file,
`LESSONS_LOG.md`, so this instruction file stays compact regardless of how
many sessions have contributed to it. Load the log with:

    skill_load(name="dftracer-annotation-lessons", file="LESSONS_LOG.md")

`session_ml_append_lesson` and `session_lessons_sync_pr` both target
`LESSONS_LOG.md` directly — new entries are appended there, after the same
anchor comment, never into this file.

## General Pitfalls (PG)

These apply to all languages (C, C++, Python).

PG1  File truncated
     Written lines < original line count → re-read the file, rewrite the complete file.

PG2  Header file annotated
     Macros placed in a .h or .hpp file → move all macros to the .c or .cpp source file.

PG3  comp= missing
     Annotation count does not equal comp= count → find and fix each gap before reporting DONE.

PG4  Lifecycle function skipped
     A *_init, *_finalize, or similar lifecycle function has a short body and was skipped via
     Rule 0 → always annotate lifecycle functions regardless of body length (see ALWAYS_ANNOTATE).

PG5  Vendor function skipped
     gpfs_*, beegfs_*, lustre_*, hdfs_*, ceph_*, daos_* functions were skipped →
     always annotate with comp="io".

PG6  Re-annotating a dirty file
     The annotated copy already contains dftracer macros from a previous run → restore the
     original (unannotated) copy first, then re-annotate from scratch.

PG7  Coverage check skipped
     Reported DONE without running the coverage verification step → always run Step 6 before
     reporting DONE. START/decorator count must equal comp= count.

PG8  UPDATE uses forward-declaration parameter name
     'param' undeclared error in UPDATE_STR/UPDATE_INT → read parameter names from the
     function definition body, not from a forward declaration.

PG9  Wrong tool name (-32002 Tool not found)
     Dot notation is not valid for MCP tools in this environment. Use the correct names:
       WRONG                        CORRECT
       todo.todoWrite               todo__todo_write
       read_file                    load
       session_read_file            dftracer__session_read_file
       session_write_file           dftracer__session_write_file
       clang_add_braces             dftracer__clang_add_braces
       clang_extract_functions      dftracer__clang_extract_functions

PG10 Revert-all on build error
     A syntax or build error caused the entire annotated file to be reverted → do NOT
     revert the whole file. Strip macros from the FAILING FUNCTION ONLY, mark it PENDING
     with a reason, and continue annotating the remaining functions. Write the new pitfall
     to lessons-learned immediately.

---

## Fortran Entry Point Pitfall (PF)

PF1  No C main() in Fortran programs
     Fortran programs (e.g. `program Flashx` in `main.F90`) have no C `main()`
     function, so DFTRACER_C_INIT/DFTRACER_C_FINI cannot be placed in the source.
     → Create a separate C wrapper file with `__attribute__((constructor))` and
     `__attribute__((destructor))` to auto-call INIT before and FINI after the
     Fortran program runs. Compile to `.o` and link it into the final binary.
     Example `dftracer_init_fini.c`:

     ```c
     #include <stddef.h>
     #include <dftracer/dftracer.h>
     __attribute__((constructor)) static void dftracer_init(void) {
         DFTRACER_C_INIT(NULL, NULL, NULL);
     }
     __attribute__((destructor)) static void dftracer_fini(void) {
         DFTRACER_C_FINI();
     }
     ```

     Add the `.o` to the link line (e.g. `ALL_OBJ_FILES` in GNU Make).
     If the Fortran linker does not fire constructors reliably (CCE `crayftn`
     observed), pivot to PRELOAD mode instead of FUNCTION/HYBRID mode.

## C-Specific Pitfalls (PC)

PC1  END after return
     DFTRACER_C_FUNCTION_END() was placed AFTER the return statement (dead code) →
     swap order: END must PRECEDE the return.

PC2  END at column 0
     DFTRACER_C_FUNCTION_END() was emitted at column 0 with no indentation →
     match the indentation of the return statement it precedes; never at column 0.

PC3  START before opening brace
     DFTRACER_C_FUNCTION_START() was placed before the opening '{' → syntax error.
     Move START to the first line INSIDE the body, after '{'.

PC4  Error macro hides exit
     MPI_CHECK / NCMPI_CHECK / H5EPRINT / HGOTO_ERROR macros internally expand to a
     hidden return or goto → do NOT add END before these macros. Only add END before
     explicit visible return statements that follow in the source.

PC5  goto: END before each goto
     Adding END before every goto statement that jumps to a shared exit label results
     in duplicate END calls → place a SINGLE END at the exit label instead, not before
     each individual goto.

PC6  Forward declaration annotated
     Annotated a line ending with ';' (a forward declaration) instead of the definition
     with a body → filter grep results to definitions only; annotate ONLY the definition.

PC7  Wrong DFTRACER_INIT value
     DFTRACER_INIT=1 is not a valid value. Valid values are:
       FUNCTION  — default and recommended (annotation-based tracing)
       PRELOAD   — use when no application annotation is done; requires LD_PRELOAD set
                   to dftracer_preload.so
       HYBRID    — both LD_PRELOAD and application annotation active
     Using an invalid value silently disables tracing.

---

## C++-Specific Pitfalls (CP)

CP1  Used DFTRACER_C_* macros in a .cpp file
     C macros do not compile in C++ translation units → replace every DFTRACER_C_*
     macro with its DFTRACER_CPP_* equivalent.

CP2  Used DFTRACER_CPP_FUNCTION() in main()
     RAII guard fires after DFTRACER_CPP_FINI(), producing a use-after-finalize span →
     replace with DFTRACER_CPP_REGION_START / REGION_END in main().

CP3  Used DFTRACER_CPP_REGION_* in a regular (non-main) function
     REGION macros are only for main() → replace with DFTRACER_CPP_FUNCTION() which
     uses the RAII pattern.

CP4  Added manual DFTRACER_CPP_FUNCTION_END() after DFTRACER_CPP_FUNCTION()
     There is no END macro for the CPP RAII guard; the destructor fires automatically on
     scope exit → remove the manual END calls.

CP5  Used UPDATE_INT in C++
     There is no DFTRACER_CPP_FUNCTION_UPDATE_INT in the C++ API → either omit the
     numeric parameter or convert it to a string before passing to FUNCTION_UPDATE.

CP6  Added #include to a .hpp header file
     dftracer includes in header files get compiled into every translation unit that
     includes the header → move the #include <dftracer/dftracer.h> to the .cpp/.cxx
     source file only.

CP7  comp= UPDATE missing
     DFTRACER_CPP_FUNCTION() count does not equal the DFTRACER_CPP_FUNCTION_UPDATE("comp",...)
     count → add a comp= UPDATE immediately after each DFTRACER_CPP_FUNCTION() call.

CP8  REGION_END missing before a return in main()
     main() has a return path that lacks DFTRACER_CPP_REGION_END before it → add
     REGION_END (and FINI if applicable) before every return statement in main().

---

## Python-Specific Pitfalls (PP)

PP1  Missing import
     ImportError at runtime: dftracer_fn or DFTracer not found →
     add: from dftracer.logger import dftracer_fn, DFTracer

PP2  comp= keyword missing from decorator
     @dftracer_fn decorator count does not equal comp= count →
     add comp="<type>" to every @dftracer_fn call.

PP3  Inconsistent cat= names across the file
     Mixed "io" / "IO" / "file" cat= values → standardise to a single consistent
     convention per file (e.g., "IO", "Compute", "MPI", "Data", "Init").

PP4  initialize_log missing from entry point
     Empty or missing trace file → add DFTracer.initialize_log(...) to every entry
     point file (top of if __name__ == "__main__" or the entry function body).

PP5  finalize_log missing
     Trace file is truncated or missing final events → add DFTracer.finalize_log()
     before every sys.exit() call and before MPI.Finalize().

PP6  @dftracer_fn placed above other decorators
     When stacked with other decorators, @dftracer_fn must be CLOSEST to the def
     statement (i.e., the last decorator before def) → move it below all other
     decorators.

PP7  @property method decorated with @dftracer_fn
     Applying @dftracer_fn to a @property accessor conflicts with the property
     descriptor protocol → skip all @property methods.

PP8  Wrong DFTRACER_INIT value when calling DFTracer.initialize_log()
     If the Python code calls DFTracer.initialize_log() explicitly, set
     DFTRACER_INIT=0 in the environment so the C-level auto-init does not double-
     initialize. Leaving DFTRACER_INIT unset while using explicit initialize_log()
     can produce duplicate or empty traces.

---

## Core Annotation Rules

### ALWAYS_ANNOTATE (never apply Rule 0 skip to these)

These function categories must always be annotated, regardless of body length or
perceived complexity:

  - Lifecycle:   *_init, *_final, *_initialize, *_finalize
  - Sync/flush:  *_fsync, *_flush, *_sync
  - File ops:    *_delete, *_rename, *_stat, *_mknod, *_getfilesize
  - Vendor FS:   gpfs_*, beegfs_*, lustre_*, hdfs_*, ceph_*, daos_*

### Rule 0 — Skip criterion

Apply Rule 0 (skip without annotation) ONLY when ALL of the following are true:
  1. The function is a pure getter or setter with no more than 5 lines
  2. It returns a single field
  3. It performs no I/O, no data movement, and no syscalls

Every Rule 0 skip must be justified by function name in the per-file report.

### comp= Classification Table

  "io"   — file I/O (POSIX open/read/write/close/stat/mmap), HDF5, NCMPI,
            backend lifecycle (init/final/initialize/finalize), vendor FS helpers
  "comm" — MPI wrappers, network I/O, distributed FS clients (S3, HDFS, RADOS, DFS)
  "mem"  — memcpy, large buffer alloc/free, mmap region setup, tensor copies
  "cpu"  — checksums, compression, encryption, hashing

C note:   transfer to/from a file (POSIX/MMAP/HDF5/NCMPI) → "io";
          transfer via network/MPI → "comm"; memcpy into mmap → "mem"
C++ note: MPI calls wrapped in C++ → "comm"; std::filesystem / fstream → "io"
Python:   comp= classification uses the same table

### Per-Function Incremental Annotation Loop (Step 1.5 rule)

Annotate ONE function at a time. After each function:

  a. Write the full file (never a partial):
       dftracer__session_write_file(run_id=..., filepath=..., content=<FULL FILE>,
         subfolder="annotated")

  b. Run the language-specific syntax check:
       C:      gcc -include /tmp/dftracer_stub.h -fsyntax-only -w -x c <file> 2>&1
       C++:    g++ -include /tmp/dftracer_stub.h -fsyntax-only -w -std=c++14 <file> 2>&1
       Python: python3 -c "import ast; ast.parse(open('<file>').read())" 2>&1

  c. PASS → mark function annotated, move to the next function.

  d. FAIL →
       i.   Identify the exact line and macro from the error message.
      ii.   Fix ONLY that macro — do not touch functions that already passed.
     iii.   Write the file and re-check (max 2 retries for this function).
      iv.   After 2 failed retries: strip macros from THIS function only,
            mark it PENDING with reason, and continue to the next function.
       v.   Write the new pitfall to the lessons file IMMEDIATELY (not at the end).

Rules that must never be broken during the loop:
  - NEVER revert annotations from functions that already passed their syntax check.
  - NEVER fix an error by reverting the whole file.
  - NEVER skip the syntax check step — it catches placement errors before the full build.
  - If BUILD ERROR MODE is active (build_errors param is set): process only the
    functions named in the errors first, then continue with unannotated functions.
