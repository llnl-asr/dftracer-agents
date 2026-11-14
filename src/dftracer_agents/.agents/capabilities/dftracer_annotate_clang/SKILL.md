---
name: dftracer_annotate_clang
description: libclang-based C/C++ annotation tools shared by the C and C++ paths: clang_extract_functions, clang_annotate_file/project, clang_insert_line, clang_add_braces, clang_write_annotated_file, clang_syntax_check, clang_lint_annotations, clang_estimate_function_cost, clang_fix_header_tentative_defs, clang_regression_test. Load when clang tools report functions: 0, already_annotated: true on an un-annotated file, 'fatal error: ... file not found' during annotation, braceless if/else bodies, END after return, FINI on only one exit of main, C/C++ annotation pitfalls (macro in #define/comment/string, forward declarations, header annotation, duplicate START/END).
---

# dftracer_annotate_clang

Converted 2026-11-13 from dftracer-agents: converted from dftracer-agents register_clang_tools (session/annotation_clang.py), skill dftracer-pitfalls (C/C++ placement pitfalls), the clang-annotator sections of dftracer-annotation-lessons. Tools keep their dftracer-agents names, signatures and behavior; the validators dftracer_annotate_clang_check, clang_syntax_check, clang_lint_annotations, clang_regression_test rule on what the predictors produce.

## Rules

- Predict with clang_extract_functions, clang_annotate_file, clang_annotate_project, clang_insert_line, clang_add_braces, clang_write_annotated_file, clang_estimate_function_cost, clang_fix_header_tentative_defs; validate with dftracer_annotate_clang_check, clang_syntax_check, clang_lint_annotations, clang_regression_test; on a failed check follow ## Corrector, then predict and validate again.

## Constraints

- Operates on the session's annotated/ tree only; never on source/ or a header unless it is template code.
- One annotation pass per file: re-running on an annotated file duplicates START/END (lint catches it).

## Corner cases

### From `dftracer-annotation-lessons/SKILL.md § Two silent-failure modes in the clang annotator (2026-08-12, laghos/MFEM)`

#### Two silent-failure modes in the clang annotator (2026-08-12, laghos/MFEM)

Both produced a **green "annotation complete"** report on a file that was in
fact left un-instrumented. Check for both explicitly — the tool's own counts are
not evidence.

##### 1. A fatal clang parse error silently yields `functions: 0`

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

##### 2. `FINI` inserted on only ONE exit path of `main` → zero app events

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

###### Root cause, and the fix (2026-08-13, found again on miniFE)

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

##### 3. Template-only projects yield `functions: 0` legitimately

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

### From `dftracer-annotation-lessons/SKILL.md § Global constructors that run before `main` silently disable all interception (2026-09-30, Enzo)`

#### Global constructors that run before `main` silently disable all interception (2026-09-30, Enzo)

**Symptom:** function events are present but there are ZERO MPI/POSIX/STDIO
events, and the trace's `"name":"end"` record shows `"bind":0` in `cfg`.
**Root cause:** an annotated constructor of a global/static object (e.g. a
registration object, a global solver instance) runs before `main`. Its
`DFTRACER_CPP_FUNCTION` lazily initializes dftracer with bind=false; the later
`DFTRACER_CPP_INIT` in `main` is then a no-op, so gotcha interceptors are never
installed. **Find them:** sort the trace's CPP_APP events by `ts` and list the
names before the first function `main` calls (e.g. the MPI init wrapper).
**Fix:** re-annotate those files with `exclude_functions=[<ctor names>]`.

### From `dftracer-annotation-lessons/SKILL.md § App headers that `#define` builtin types break dftracer.h (2026-09-30, Enzo)`

#### App headers that `#define` builtin types break dftracer.h (2026-09-30, Enzo)

`#define int long_int` / `#define float double` in an app header mangle
dftracer's own headers when `#include <dftracer/dftracer.h>` comes after it
("redefinition of 'long_int' as different kind of symbol", "typedef
redefinition with different types"). dftracer.h is self-contained, so it must
be the FIRST include. `clang_annotate_file` now inserts it before the first
top-level `#include` (previously after the last one).

### From `dftracer-annotation-lessons/SKILL.md § main reached through a macro, and exits that never return (2026-09-30, Enzo)`

#### main reached through a macro, and exits that never return (2026-09-30, Enzo)

- `Eint32 MAIN_NAME(...)` + `#define MAIN_NAME main`: entry detection now
  follows `#define X main`. Check the INIT landed in the right function.
- If the app always leaves through a `my_exit()`-style function that calls
  `MPI_Finalize` then `exit()`, the FINI the annotator puts at the end of
  `main` never runs. Insert `DFTRACER_*_FINI()` inside that function right
  before the MPI finalize call.

### From `dftracer-pitfalls/SKILL.md § Annotation Pitfalls (lessons learned)`

#### Annotation Pitfalls (lessons learned)

> **Quick navigation:** For the top-10 most common mistakes and critical rules,
> see the `dftracer-cheatsheet` skill first. This file contains the full detail for
> every pitfall, organized in the order they were discovered.

These are real mistakes that caused build failures or missing trace data in actual
annotation sessions. Check pitfalls matching your current file type before writing.

**Standing instruction — update `.goose/hints/02-annotation-pitfalls.md` in two situations:**

1. **Immediately after any fix during annotation** — the moment you fix a build error
   or annotation mistake, append a new entry.
2. **At end of every session (Pass 5, Step 2)** — scan `annotation_process.log` and
   add entries for every pattern not yet covered.

When you encounter ANY error during annotation, build, or run that is not already
covered, and you fix it, you MUST immediately append a new entry using the format:

```
---
context: <one-line description of what was being attempted>
error: |
  <exact error message or key excerpt>
root_cause: <why it happened>
fix: |
  <exact rule or steps that resolved it>
tags: [<language>, annotation, <error-keyword>, ...]
---
```

---

**Pitfall: `{` inside a string literal**

context: Macro injected into fprintf multi-line string
error: `aiori-MPIIO.c:241: error: missing terminating " character`
fix: Verify the `{` is a real function body opener: must follow a closing `)`, be last on its line, and NOT be inside a string. If the line contains `"..."` or is inside `fprintf`, it is NOT a function body.

---

**Pitfall: Macro inside `#define` body**

error: `'data_fn' undeclared — DFTRACER_C_FUNCTION_END expanded inside a macro`
fix: Never annotate `#define` macro bodies, inline lambdas, or function-like macros. Only annotate real C function definitions.

---

**Pitfall: Macro inside block comment**

error: START inside `/* ... */` — no compile error but function is silently never traced.
fix: Skip any line where `{` appears after `/*` with no intervening `*/`.

---

**Pitfall: START inside a control-flow block**

error: Multiple START macros per function when `if(...) {` or `for(...) {` matched as function openers.
fix: Reject any match where the "function name" is: `if`, `else`, `for`, `while`, `do`, `switch`, `case`, `return`, `sizeof`, `typeof`, `namespace`, `class`, `struct`, `union`, `enum`, `template`, `catch`, `try`.

---

**Pitfall: Forward declaration annotated**

error: `DFTRACER_C_FUNCTION_START injected after a semicolon-terminated prototype`
fix: A function DEFINITION must have `{` (not `;`) after the parameter list. Never annotate a line ending with `;`.

---

**Pitfall: struct/union/enum annotated as function**

error: `DFTRACER_C_FUNCTION_START() inserted after "struct IOR_param_t {"`
fix: Reject any match where the return-type token is `struct`, `union`, `enum`, `typedef`, `class`, or `namespace`.

---

**Pitfall: File truncated during write**

error: `ior.c went from 1869 lines to 960 lines after session_write_file`
fix: Always write the COMPLETE file. Read the original, build the full annotated content in memory, verify line count is ≥ original, then write in one call.

---

**Pitfall: Annotating header files (.h/.hpp)**

error: `DFTRACER_C_FUNCTION_START injected into inline function in header included by many TUs — redeclaration or scope errors`
fix: Do NOT annotate header files. Only annotate .c/.cpp/.cxx source files. Add `#include <dftracer/dftracer.h>` to the .c file, not the header.

---

**Pitfall: UPDATE uses parameter name from declaration, not definition**

error: `aiori-POSIX.c:687:50: error: 'count' undeclared`
fix: Always locate the function DEFINITION (with `{` body) to read exact parameter names. The forward declaration often omits or changes names. Use: `grep -n "POSIX_Xfer" file.c` to find both, then use names from the definition.

---

**Pitfall: START placed before `{` instead of after it**

error: `data_fn undeclared or duplicate-START errors`
fix: START must go on the FIRST LINE AFTER the opening `{`, not before it.

---

**Pitfall: END at column 0**

error: Indentation mismatch — END at column 0 while surrounding code is indented.
fix: END indentation must match the line it precedes. Check the `return` or `}` indentation and use the same leading whitespace.

---

**Pitfall: Duplicate STARTs or misplaced ENDs from iterative annotation**

fix: Always restore from the original unannotated source before re-running any annotation script: `cp source/src/foo.c annotated/src/foo.c`. Never annotate a file that already contains dftracer macros.

---

**Pitfall: END placed after return statement in MPI-IO functions**

error: `aiori-MPIIO.c: error: 'data_fn' undeclared — DFTRACER_C_FUNCTION_END placed after a return`
fix: For functions with complex control flow, manually trace EVERY exit path. List all returns with `grep -n "return" file.c`. Place END immediately BEFORE each return, same indentation. For switch/case: place END before `break` or `return` in each case.

---

**Pitfall: Duplicate END macros**

error: `conflicting types for 'finalize_region'` — two consecutive END macros.
fix: Before inserting any END, check the 3 lines above the return: `awk "NR>=$(( LINE-3 )) && NR<$LINE" file.c | grep -c "DFTRACER_C_FUNCTION_END"`. If count is already 1, skip the insertion.

---

**Pitfall: DFTRACER_INIT=1 is incorrect value**

error: Trace file is empty (0 bytes or 580 bytes with no events).
fix: NEVER set `DFTRACER_INIT=1` when source already contains explicit `DFTRACER_C_INIT()` calls. Pass `env_extra='{"DFTRACER_INIT": "FUNCTION"}'`. Heuristic: `grep -r "DFTRACER_C_INIT" annotated/` — if matches found, set `DFTRACER_INIT=FUNCTION`.

---

**Pitfall: .deps missing separator after Makefile patching**

error: `.deps/IOR-aiori-DUMMY.Po:1: *** missing separator. Stop.`
fix: Always purge .deps before rebuilding after a Makefile patch: `rm -rf .deps src/.deps && make clean && make -j4`.

---

**Pitfall: dftracer.h not found / linker undefined references**

error: `fatal error: dftracer/dftracer.h: No such file or directory` + `undefined reference to 'initialize_main'`
fix: Patch the build system BEFORE annotating any file:
1. Add `-I<install_ann>/include` to CFLAGS
2. Add `-L<install_ann>/lib -Wl,-rpath,<install_ann>/lib` to LDFLAGS
3. Add `-ldftracer_core -lcpp-logger -lbrahma` to LIBS
For autotools: patch `src/Makefile` (not just the top-level). Then: `rm -rf .deps src/.deps && make -j4`.

---

**Pitfall: Smoke test binary not found (return code 127)**

error: `./ior: not found (return code 127)`
fix: Always use the FULL ABSOLUTE PATH to the binary. Find it first: `find <workspace> -name "ior" -type f -not -path "*/source/*"`.

---

**Pitfall: Error-checking macro (MPI_CHECK, NCMPI_CHECK, H5EPRINT) hides early return**

error: Traces show START with no corresponding END — span imbalance, no compile error.
fix: Before annotating any function, grep for all error-checking macro calls and place END immediately before them. Verify after annotation: count of END >= count of (return + CHECK_MACROS) in the function.

---

**Pitfall: goto-based error handling — END before goto vs at label**

error: N duplicate END macros when a function has N `goto done` statements.
fix: Place a SINGLE END at the goto label, not before each goto: `done: DFTRACER_C_FUNCTION_END(); ... return ret;`

---

**Pitfall: Multi-line function signature not matched**

error: MPIIO_Xfer annotation skipped / START placed inside wrong line.
fix: Treat any `{` on its own line as a potential function-body opener. Look UPWARD from that `{` to find the function signature — scan back until you find a line ending with `)`.

---

**Pitfall: MPI-IO backend annotation fails and must be reset**

error: `error: 'data_fn' undeclared (first use in this function)` — file reset to include-only.
fix: Annotate MPI-IO style backends LAST and one function at a time. Pick the simplest first (MPIIO_Close, MPIIO_Delete). Read the entire function body before writing a single macro. Build after every single function.

---

**Pitfall: POSIX_Xfer forward declaration annotated instead of definition**

fix: Before annotating ANY function, search for ALL occurrences of its name: `grep -n "POSIX_Xfer" file.c`. If two hits: the one with no param names and trailing `;` is the forward declaration — DO NOT ANNOTATE. Annotate only the one with named parameters and a `{` body.

---

**Pitfall: Vendor-specific functions (gpfs_*, beegfs_*, lustre_*) not annotated**

error: Coverage check found gpfs_free_all_locks, beegfs_getStriping, etc. unannotated.
fix: After annotating named backend functions, always scan the ENTIRE file: `grep -n "^[a-zA-Z].*(.*)$\|^static.*(.*)$" file.c | grep -v ";" | grep -v "DFTRACER"`. These are I/O operations — annotate with `comp="io"`.

---

**Pitfall: Lifecycle functions skipped because body is short**

error: POSIX_Initialize and POSIX_Finalize not annotated (bodies only contain `#ifdef` blocks).
fix: Backend lifecycle functions (*_Initialize, *_Finalize) are ALWAYS annotated regardless of body size. Never apply Rule 0 to lifecycle functions.

---

**Pitfall: Sync and rename functions missed**

error: POSIX_Sync and POSIX_Rename not annotated — not "read/write" functions.
fix: The mandatory list includes: Create, Open, Xfer, Close, Delete, Fsync, Sync, Rename, GetFileSize, Mknod, Initialize, Finalize.

---

**Pitfall: Skipping POSIX_Mknod because "deprecated"**

fix: Never skip a function solely because it is described as "deprecated" or not called in the basic smoke test. Only skip under Rule 0 (pure getter/setter ≤5 lines, no I/O).

---

**Pitfall: comp= not added to any function in a pass**

error: grep for comp= finds zero results but grep for START finds 21.
fix: After annotating a file, always run: `grep -c "DFTRACER_C_FUNCTION_START" file.c` vs `grep -c 'DFTRACER_C_FUNCTION_UPDATE_STR.*comp' file.c`. If different, find each bare START: `grep -A2 "DFTRACER_C_FUNCTION_START" file.c | grep -v "comp"`.

---

**Pitfall: dftracer header added to a .h file instead of .c file**

error: `fatal error: dftracer/dftracer.h: No such file or directory` in files that never include dftracer themselves.
fix: Move `#include <dftracer/dftracer.h>` into each .c/.cpp file that contains annotation macros. Never add it to a .h header.

---

**Pitfall: Linker fails with "undefined reference to dftracer_*"**

error: `/usr/bin/ld: foo.o: undefined reference to 'dftracer_init'`
fix: Add to src/Makefile: `LDFLAGS += -L$(DFTRACER_PREFIX)/lib -Wl,-rpath,$(DFTRACER_PREFIX)/lib` and `LIBS += -ldftracer_core`. If dftracer installed via pip/venv, use: `DFTRACER_LIB=$(python3 -c "import importlib.util, pathlib; p=importlib.util.find_spec('dftracer'); print(pathlib.Path(p.origin).parent / 'lib')")`.

---

**Pitfall: Binary links but crashes — dftracer .so not found at runtime**

error: `./myprogram: error while loading shared libraries: libdftracer_core.so: cannot open shared object file`
fix: Add `-Wl,-rpath,$(DFTRACER_PREFIX)/lib` to LDFLAGS. Or export `LD_LIBRARY_PATH=$(DFTRACER_PREFIX)/lib` before running.

---

**Pitfall: Vendor-specific functions not appearing in trace**

error: gpfs_access_start, beegfs_getStriping annotated but not in trace output.
fix: This is EXPECTED — they are guarded by `#ifdef HAVE_GPFS_FCNTL_H` etc. and only invoked on those filesystems. Report as "annotated, not traced (filesystem not available in test environment)".

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector

dftracer_annotate_clang_check names the failed check:
- `compiles` false: run clang_syntax_check for the full diagnostic; a missing header -> pass compile_flags / extra_include_dirs (functions: 0 is the symptom of a fatal parse error); a broken insertion -> clang_lint_annotations, then re-annotate with clang_annotate_file.
- `END before every exit` false with "without braces": clang_add_braces(run_id, file) -> clang_annotate_file(write_immediately=False) -> clang_syntax_check -> clang_write_annotated_file.
- `comp UPDATE first` / `one START` false: the file was annotated twice or edited by hand; restore it from source/ and run clang_annotate_file once.
- `main` checks false: re-run clang_annotate_file (it resolves every exit of main to the teardown call above it); if main is reached through a macro or exits via my_exit(), insert FINI by clang_insert_line before the MPI finalize call.
- `critical I/O calls annotated` false: annotate those functions (they were skipped by Rule 0 or hidden by a parse error).
Re-run the check; the same failure twice stops for the user.

## Self-heal

- A clang tool that mis-places a macro is fixed in the tool (this capability) with a regression case for clang_regression_test, never by hand-patching every file.

## Self-learn

- What is explored here becomes a capability: see capability_builder. A new annotation failure is a lesson here (LESSONS.md, symptom -> root cause -> fix, dated); a new verified recipe is a WORKED.md entry; a new workload or software met while annotating becomes workload_<x> / software_<x>.

## What works

Verified recipes, dated, as in WORKED.md (full list there):

- [rajaperf/annotate/clang-add-braces-add-braces-c-silently-corrupted] 2026-08-05: Two-part fix in source_parser.py / annotation_clang.py: 1. _add_braces_via_clang now checks clang's exit code. Any nonzero exit (parse did not complete cleanly) means the AST is untrustworthy for line-range accuracy -- return a safe no-op ({"modified": False, "skipped_reason": "..."}) instead of proceeding. This converts silent corruption into an explicit, visible signal. 2. add_braces_c/_add_braces_via_clang now accept an extra_include_dirs parameter; clang_annotate_file and clang_add_braces both pass a new shared helper's discovered dirs (dftracer install include/, annotated/<file-dir>, annotated/src) -- a best-effort improvement for simpler projects, NOT a claim of completeness for deep submodule trees like RAJA/BLT (a proper fix for those would read a CMake-generated compile_commands.json, not implemented yet -- flag as a future improvement if this recurs on another BLT/CMake-submodule-heavy app). 3. clang_annotate_file's response now surfaces `braces_skipped_reason` so the calling agent SEES that brace insertion was skipped and can manually verify (via grep/read) whether the file has any real braceless if/for/while bodies needing correct END-macro placement, rather than silently assuming "no insertions" meant "nothing needed wrapping". 4. For THIS session, the two already-corrupted files were fixed by reconstructing them from source/ with ONLY the legitimate dftracer insertions re-applied (the #include and the 4 correct DFTRACER_CPP_FUNCTION()/UPDATE pairs at Executor::runSuite/runKernel and KernelBase::execute/runKernel, all previously verified correct by the build-smoke agent) -- not a patch-over of the corrupted file. General takeaway: on ANY project where clang_syntax_check ALSO reports missing-header errors for a file (check this first), clang_add_braces is operating on a degraded AST for that same file -- after this fix it will safely skip rather than corrupt, but any real braceless control-flow body in that file needs manual verification. (resolved: Neither an if/for/while-overlap shape nor a multiline-call shape — this time the corrupted ranges don't overlap each other at all, so the existing overlap/subse)
- [rajaperf/annotate/linking-raja-perf-exe-failed-with-pasting-formed] 2026-08-05: 1. dftracer.h ground truth (read directly, not assumed): DFTRACER_CPP_REGION_START(name)/_END(name) take a BARE IDENTIFIER (no quotes) -- the trace event name is derived internally via #name stringization, so DFTRACER_CPP_REGION_START(main) is already correct and sufficient; do not add manual quotes. DFTRACER_CPP_REGION_DYN_UPDATE(name, key, val) -- not DFTRACER_CPP_FUNCTION_UPDATE(key, val) -- is the correct UPDATE call paired with REGION_START/END (arrow-deref on the profiler_##name pointer REGION_START declares, vs. FUNCTION_UPDATE's hardcoded profiler_dft_fn which only DFTRACER_CPP_FUNCTION() ever declares). 2. Fixed in annotation_clang.py's clang_annotate_file: `fn_start`/`fn_end` for a C++ main() are now `DFTRACER_CPP_REGION_START(main);` / `DFTRACER_CPP_REGION_END(main);` (bare identifier). `_make_update()` gained a `region_name` parameter -- when set, it emits `DFTRACER_CPP_REGION_DYN_UPDATE({region_name}, "comp", "{comp}");` instead of the FUNCTION_UPDATE form; both call sites for is_main_fn pass `region_name="main"`. 3. Verified with an ACTUAL clang -fsyntax-only compile against the real installed dftracer.h (not just visual inspection) before considering this fixed -- exit 0. Re-ran the tool end-to-end on a fresh synthetic MPI main() to confirm the generated output is the corrected form. 4. RAJAPerfSuiteDriver.cpp (this session's actual file, corrupted before this fix existed) was hand-corrected to match: 3 lines changed (REGION_START/END unquoted, UPDATE call switched to DFTRACER_CPP_REGION_DYN_UPDATE). General takeaway: a macro-usage fix for a C/C++ annotation tool is NOT verified until it has actually been compiled against the real vendor header -- copying a pattern from an agent's own prose report (even one that "looked plausible" and passed clang_lint_annotations, which only checks ordering/placement, not argument well-formedness against the real macro definitions) is not verification. This exact class of error is invisible until final link of a large multi-file build, far downstream of where it was introduced. (resolved: DFTRACER_CPP_REGION_START("main")/DFTRACER_CPP_REGION_END("main") -- WITH QUOTES -- fails to compile: dftracer.h defines these as `profiler_##name` token-pastes)
- [minife/annotate/main-teardown] 2026-08-13: REGION_END + FINI placed before both `miniFE::finalize_mpi()` lines (teardown wrapper, not MPI_Finalize) and no closing-brace anchor after `return`; took a 16-rank run from 0 to 18,789 CPP_APP events. clang_annotate_file now selects the same lines (_falls_through, *_mpi wrapper regexes). (dftracer-annotation-lessons, two silent-failure modes)
- [laghos/annotate/compile-flags] 2026-08-12: pass compile_flags with the project's -I/-D to clang_extract_functions / clang_annotate_file / clang_annotate_project; a fatal parse error otherwise yields functions: 0 (0 of 15 solver functions). (dftracer-annotation-lessons)
- [enzo/annotate/project] 2026-09-30: clang_annotate_project with the process pool (`jobs`, default min(32, cpus)) and real compile flags annotated 1061 files in ~4 min (serial took hours). (dftracer-annotation-lessons, Speed)
- [enzo/annotate/global-ctors] 2026-09-30: re-annotate files whose global/static constructors run before main with exclude_functions=[<ctor names>], so DFTRACER_CPP_INIT in main binds the interceptors. (dftracer-annotation-lessons)

### From `dftracer-annotation-lessons/SKILL.md § Speed: annotate projects in parallel (2026-09-30)`

#### Speed: annotate projects in parallel (2026-09-30)

clang's JSON AST dump of one Enzo `.C` file is ~370 MB (it includes every
header declaration), so serial annotation of ~1000 files takes hours.
`clang_annotate_project` now runs files in a process pool (`jobs`, default
min(32, cpus)); Enzo's 1061 files took ~4 min. Pass real compile flags
(defines + `-I`), or the parse fails and the file is skipped.
