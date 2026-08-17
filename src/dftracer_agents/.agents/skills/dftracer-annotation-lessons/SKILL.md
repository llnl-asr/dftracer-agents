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

PC8  MCP tool places END() before every error-checking macro, fragmenting the trace span
     MCP clang_annotate_project tool places DFTRACER_C_FUNCTION_END() before EVERY
     error-checking macro (HDF5_CHECK, MPI_CHECK, etc.) that contains implicit exit
     logic, instead of once before the function's actual return statement. This violates
     dftracer-annotate-c Rule E (error-checking macros should NOT have END before them).
     Observed in session ior/20260724_175545 with HDF5_Open() getting 15 extra END()
     calls (lines 270, 273, 280, 307, 312, 331, 335, 343, 360, 363, 369, 374, 378, 391,
     395, 418), creating fragmented spans instead of one continuous function span.
     → Fix: manually remove all intermediate END() calls before error-check macros; place
     a single END() before the function's actual return statement. This is a known MCP
     tool limitation requiring post-processing cleanup for HDF5-heavy code. See also
     [[workload-ior]] for this same issue.

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

CP10 Annotated a hot-loop/polling function in an async communication engine
     A function called in a tight spin/poll loop (e.g. a progress-engine
     function like `while (not fn()) { local_progress(); }`, or any function
     whose body is itself a `while(true)`/`MPI_Test` polling loop) was given
     DFTRACER_CPP_FUNCTION() → RAII overhead (timestamp capture + buffer
     write) accumulates across millions of calls and DOMINATES wall-clock
     time, causing the traced binary to run 5-10x+ slower than untraced, with
     runtime NOT scaling with workload size (`-n`/iteration count) the way an
     untraced run does — a signature symptom, not just "slow". Confirmed on
     YGM (llnl/ygm): `comm::local_progress()`, `local_wait_until()`,
     `process_receive_queue()`, `flush_next_send()`, `check_completed_sends()`,
     `check_if_production_halt_required()`, `post_new_irecv()`,
     `local_process_incoming()`, `handle_completed_send()` are all called from
     one shared progress-polling path and were incorrectly annotated.
     → Identify hot-loop candidates BEFORE annotating: grep for functions
     whose callers include a `while`/spin-wait loop, or that are called from
     another annotated "progress"/"poll"/"wait_until"-style function. Strip
     DFTRACER_CPP_FUNCTION() (or the C/Python equivalent) from these — keep
     annotation only on the coarser, logically-once-per-operation entry
     points (e.g. `async`, `barrier`, `all_reduce*`, `mpi_send/recv/bcast`)
     that a user's application code actually calls, not the internal
     mechanics run underneath. Diagnostic tell: run the SAME workload size
     with DFTRACER_ENABLE=1 vs DFTRACER_ENABLE=0 — if traced is many times
     slower AND multiple different `-n` values under tracing converge to
     near-identical wall time (rather than each scaling proportionally like
     the untraced runs do), that's this pattern, not a real communication
     bottleneck.

     PREFERRED FIX (confirmed working, YGM 2026-08-04): don't strip the
     annotation -- use dftracer's SELECTIVE AGGREGATION instead, so hot
     functions keep visibility but their many short calls collapse into
     periodic summary events instead of one raw event each. Identify
     candidates from SMOKE TEST timing, not just static hot-loop grepping:
     any annotated function whose observed per-call duration in the smoke
     trace is very small (rule of thumb: `dur < 1000` in the configured
     DFTRACER_TIME_METRIC unit) is a selective-aggregation candidate even if
     it wasn't caught by the caller-pattern heuristic above.
     ```bash
     export DFTRACER_ENABLE_AGGREGATION=1
     export DFTRACER_AGGREGATION_TYPE=SELECTIVE   # exact string, uppercase
     export DFTRACER_AGGREGATION_FILE=<path-to-rules.yaml>
     export DFTRACER_TRACE_INTERVAL_MS=5000       # bucket granularity -- scale to job length, see below
     ```
     Bucket granularity should scale with expected job duration, not stay
     fixed: for jobs under ~5 minutes use a ~1000ms (1s) interval so
     aggregated buckets still resolve phase changes; for longer jobs grow the
     interval roughly proportionally (e.g. tens of minutes -> several seconds,
     hour+ -> tens of seconds) so the aggregated event count stays bounded
     without collapsing a short run's timeline into 1-2 buckets.
     rules.yaml content (top-level keys are `inclusion`/`exclusion`, NOT
     nested under an `aggregation:` key -- that nesting is only for the
     separate main DFTRACER_CONFIGURATION YAML file):
     ```yaml
     inclusion:
       - "dur < 1000"
     exclusion: []
     ```
     Rule syntax is `field OP value` (`==`,`!=`,`>`,`>=`,`<`,`<=`,`IN {...}`,
     `LIKE "pattern"`, `AND`/`OR`/`NOT`) evaluated against each event's
     fields (`dur` = duration in the configured DFTRACER_TIME_METRIC unit,
     plus `name`/`cat`/any UPDATE'd metadata key). Events matching an
     inclusion rule (and no exclusion rule) get bucketed per
     DFTRACER_TRACE_INTERVAL_MS instead of logged individually; the output
     aggregated event (`"ph":3` in the .pfw JSON) carries `dft_cnt` (call
     count folded in) plus `dur_sum`/`dur_min`/`dur_max` and per-numeric-arg
     sum/min/max. Verified on YGM: with all 9 hot-loop functions annotated
     AND this aggregation config, a 128-rank run went from "traced slower
     than untraced and non-scaling with -n" back to normal near-linear
     scaling (n=500 -> 70.2s, closely matching the untraced-baseline rate),
     with per-rank trace files shrinking from >1GB (unaggregated) to
     under 1MB (aggregated) -- full CPP_APP-category visibility retained
     (176 aggregated hot-function events with real dft_cnt in the small run)
     at a fraction of the volume and overhead. Prefer this over fully
     stripping annotation whenever the hot function's behavior is itself
     diagnostically interesting; strip entirely only when even the
     aggregation bookkeeping overhead is too much (very extreme call rates).

CP9  #include <dftracer/dftracer.h> placed after the library's own headers in a
     header-only library whose implementation lives in a .ipp included at the
     bottom of a .hpp (e.g. class.hpp includes class.ipp, and class.ipp's method
     bodies use DFTRACER_CPP_* macros). If the entry .cpp includes the library
     header (e.g. <ygm/comm.hpp>) BEFORE <dftracer/dftracer.h>, every dftracer
     macro used inside the pulled-in .ipp/.hpp headers is undefined at parse
     time → compile errors deep inside the library, not at the include site.
     → In the entry .cpp, <dftracer/dftracer.h> MUST be the very first include,
     before any of the library's own headers. Confirmed on YGM (llnl/ygm),
     comm.ipp/collective.hpp/mpi.hpp/comm_environment.hpp all use DFTRACER_CPP_*
     macros and are pulled in transitively by <ygm/comm.hpp>.

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

PP9  Module-level (script-scope) I/O code is unreachable by decorator tools
     Code executed at import time — outside any `def` — such as a top-level
     `tarfile.open(...).extractall(); ...close()` block, cannot be annotated by
     any python_* decorator tool (they only attach to function definitions).
     Symptom: real I/O happens but no trace event covers it, and the annotation
     tool reports the file as "clean" with nothing to fix.
     Fix: manually wrap the block in a `with DFTracerFn("<cat>", name="<name>"):`
     context manager. This is a legitimate scoped manual fixup, not a bypass of
     the Tool-First Rule, because no MCP annotation tool can express edits to
     non-`def` code. Seen in `mutation_overlap.py` / `frequency.py` in the
     1000genome-workflow session (see [[workload-1000genome]]).

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

### DFTRACER_C_INIT third argument is `int*`, never a literal int

**Symptom:** `error: incompatible integer to pointer conversion passing 'int'
to parameter of type 'int *'` on a constructor like
`DFTRACER_C_INIT(NULL, NULL, -1);`, often followed by dozens of unrelated
cascading "function definition is not allowed here" errors for the rest of
the file — the misparse from this single bad statement corrupts the parser
state for everything after it, especially under Cray clang's strict C99.

**Root cause:** `DFTRACER_C_INIT(log_file, data_dirs, process_id)` expands to
`initialize_main(log_file, data_dirs, process_id)`, where `process_id` is
declared `int* process_id` in `dftracer/dftracer.h`. Passing a literal `-1`
(an `int`) is a genuine type mismatch — merely a warning on lenient
compilers, fatal under strict `-std=c99`. This was a systemic default-value
bug in the annotation tooling, not specific to any one codebase — it just
happened to be caught here because of Cray clang's strictness.

**Fix:** `clang_annotate_file`/`clang_annotate_project`'s `init_args` default
was changed from `"NULL, NULL, -1"` to `"NULL, NULL, NULL"` (2026-07-16).
When hand-fixing an already-annotated file, use `DFTRACER_C_INIT(NULL, NULL,
NULL)` (or a real `int` variable's address) — never a literal int.

Related: `DFTRACER_C_METADATA(name, key, val)` expands to a `{ ... }`
compound-statement block (it declares/initializes/finalizes a local
`struct DFTracerData*`), so it must be called from inside a function body —
never at file/global scope. If app-parameter metadata annotation is placed
at global scope (e.g. right after the `#include <dftracer/dftracer.h>`
line, sibling to the constructor), move those calls inside the
`__attribute__((constructor))` init function, after `DFTRACER_C_INIT`.

## Two silent-failure modes in the clang annotator (2026-08-12, laghos/MFEM)

Both produced a **green "annotation complete"** report on a file that was in
fact left un-instrumented. Check for both explicitly — the tool's own counts are
not evidence.

### 1. A fatal clang parse error silently yields `functions: 0`

C++ projects define the interesting work as out-of-line class methods
(`void LagrangianHydroOperator::Mult(...)`). If clang cannot find a header, that
is a **fatal** error which aborts the parse; the class declaration is never seen,
so every method defined against it disappears from the AST. The extractor then
legitimately finds nothing, and `clang_annotate_project` reported
`status: ok, functions: 0, insertions: 1` (the 1 being the `#include`).
Measured: 0 of 15 solver functions annotated.

Two compounding causes, both now fixed in the tools:
- No way to pass include paths → added `compile_flags` to
  `clang_extract_functions` / `clang_annotate_file` / `clang_annotate_project`.
- `clang_annotate_file` parses a **temp copy in the system temp dir**, which
  discards the original directory, so *quoted* includes
  (`#include "laghos_solver.hpp"`) cannot resolve → the tool now auto-adds
  `-I<original file's dir>` and `-I<annotated/ root>`.
- Both tools now return `status: "error"` (not `ok`) when clang reported a
  `fatal error:` and zero functions were extracted.

**Verify, always:** `grep -c DFTRACER_ <file>` per file after annotating, and
sanity-check the count against the number of function definitions you expect.
Beware `grep DFTRACER` alone: the inserted `#include <dftracer/dftracer.h>` is
**lowercase**, so a case-sensitive grep for `DFTRACER` can report 0 on a file
that did get the include — grep for the macros (`DFTRACER_CPP_`/`DFTRACER_C_`)
to count real instrumentation.

### 2. `FINI` inserted on only ONE exit path of `main` → zero app events

The annotator put `REGION_END`+`FINI` at the first `return` it found in `main`
and nowhere else. On any other exit — including the normal `return 0` — finalize
never ran, the logger never flushed, and the trace contained **no `CPP_APP`
events at all**.

This is dangerous because the trace still looks populated: brahma's GOTCHA
interception (`POSIX`/`STDIO`) and the PAPI sampler flush independently of the
annotation logger, so you get a multi-MB trace with plausible I/O and counter
activity and *no* application functions. Symptom to watch for: a trace whose
categories include POSIX/STDIO/PAPI but where `CPP_APP` (or `C_APP`) is absent
or tiny.

Check the balance before building, and keep `FINI` before any `MPI_Finalize()`:

```bash
grep -n "REGION_START\|REGION_END\|_FINI\|_INIT" <entry>.cpp
# then: every `return` inside main() needs END+FINI ahead of it
```

#### Root cause, and the fix (2026-08-13, found again on miniFE)

The same bug reproduced verbatim on miniFE — `REGION_END`+`FINI` emitted *after*
`return return_code;`, plus nothing at all on the early-exit path. Root-caused
and **now fixed in `annotation_clang.py`**. Two compounding defects:

1. **Teardown was detected by the literal symbol only.** The pre-scan matched
   `\bMPI_Finalize\s*\(` and nothing else. Both laghos and miniFE call a
   *wrapper* (`miniFE::finalize_mpi()`), so `_mpi_finalize_line` stayed `None`
   and placement fell back to the closing brace — which is *after* the trailing
   `return`, i.e. dead code. Same for startup: the early-exit END/FINI pass was
   gated on `_mpi_init_line is not None`, and `miniFE::initialize_mpi(...)` never
   matched `MPI_Init`, so that pass was skipped entirely. Both regexes now also
   match `(initialize|init|setup|start)_mpi(...)` /
   `(finalize|cleanup|shutdown|stop)_mpi(...)` and the `mpi_*` orderings.

2. **Anchoring at the closing brace is only valid for a fall-through function.**
   Insertions land immediately *before* the `}`, which is correct for a void
   function but dead when the last statement is `return`. Added
   `_falls_through(lines, close_brace_line)`, which walks up past blanks,
   comments and preprocessor lines and reports whether the last executable
   statement terminates (`return`/`exit`/`abort`/`throw`/`goto`/...).

Placement is now: resolve **every** exit of `main()` to the nearest MPI teardown
call above it with no other exit in between (keeping FINI before `MPI_Finalize`,
lint rule L3), else to the exit itself; add a closing-brace anchor only when
`_falls_through` is true. The same fall-through fix applies to regular C
functions, where the old `if exits / else close-brace` split silently dropped
the END on a function that *both* returns early and falls through.

Verified against pristine miniFE `main.cpp`: the tool now selects exactly the two
`miniFE::finalize_mpi()` lines and no closing-brace anchor — identical to the
hand-fix that took a 16-rank run from 0 to 18,789 `CPP_APP` events.

### 3. Template-only projects yield `functions: 0` legitimately

Header-only / template-heavy C++ (miniFE, and any Kokkos- or Thrust-style code)
puts the real work in `.hpp` templates that **cannot be parsed standalone**. The
clang tools then return nothing for those files — correctly, but the effect is
the same as a silent failure: `main.cpp` gets instrumented and the entire solver
does not.

Do not fight the tool here. Annotate template headers with an explicit,
anchor-driven script (insert `DFTRACER_CPP_FUNCTION()` +
`DFTRACER_CPP_FUNCTION_UPDATE("comp", ...)` after the function's opening brace),
keep the anchor list in the session's `scripts/` so the placement is auditable,
and be deliberate about which functions you *exclude* — per-element and
per-timer-tick helpers will otherwise dominate the trace. See
[[workload-minife]] for a worked example with its exclusion list.

Substring anchors need care: an anchor of `exchange_externals(MatrixType& A,`
also matches `begin_exchange_externals(MatrixType& A,`. Make the script
idempotent (skip if the next line already has the macro) and print one line per
insertion so the result can be reviewed.
