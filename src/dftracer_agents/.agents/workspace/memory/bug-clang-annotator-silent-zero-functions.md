---
name: bug-clang-annotator-silent-zero-functions
description: clang annotator reported ok/0-functions on unparseable C++ (out-of-line methods vanish) and placed END/FINI where they never execute — both now FIXED in the tools; both produce a plausible-looking but app-event-free trace
metadata:
  type: project
---

Two independent silent-failure modes in the clang annotation tools, both of which
returned a GREEN report while leaving code un-instrumented. Found on laghos (MFEM).

**1. Fatal clang parse error → `status: ok, functions: 0`.** In C++ the work lives in
out-of-line class methods (`void LagrangianHydroOperator::Mult(...)`). A header clang
cannot find is a *fatal* error that aborts the parse, so the class declaration is
never seen and every method against it disappears from the AST. Measured: 0 of 15
solver functions annotated, reported as success. Fixed in
`annotation_clang.py` / `source_parser.py`:
- added `compile_flags` to `clang_extract_functions` / `clang_annotate_file` /
  `clang_annotate_project`, threaded down to the clang invocation;
- `clang_annotate_file` parses a temp copy in the system temp dir, which broke
  *quoted* includes — it now auto-adds `-I<original file dir>` and `-I<annotated root>`;
- both tools now return `status: "error"` when clang emitted `fatal error:` and zero
  functions came back, instead of a misleading `ok`;
- `_try_clang` records clang stderr/fatal lines into a `diagnostics` dict and no
  longer discards them; `-x<lang>` is appended last so a caller's `-x hip` cannot
  hijack language selection.

**2. `FINI` on only ONE exit of `main` → zero app events. NOW FIXED (2026-08-13).**
The annotator placed `REGION_END`+`FINI` at the first `return` in `main` only. On every
other exit, including the normal `return 0`, finalize never ran and the logger never
flushed, so the trace had NO `CPP_APP` events — while still looking healthy, because
brahma's GOTCHA interception (POSIX/STDIO) and the PAPI sampler flush independently. After
adding END+FINI to all five laghos exits, a 1-rank smoke went 0 → 24,123 CPP_APP events.

Reproduced verbatim on miniFE (END+FINI emitted *after* `return return_code;`, nothing on
the early-exit path), then root-caused and fixed in `annotation_clang.py`. Two compounding
defects:

- **Teardown was matched by literal symbol only** (`\bMPI_Finalize\s*\(`). Both laghos and
  miniFE call a *wrapper* (`miniFE::finalize_mpi()`), so the anchor stayed `None` and
  placement fell back to the closing brace — after the trailing `return`, i.e. dead code.
  The early-exit pass was separately gated on `_mpi_init_line is not None`, and
  `miniFE::initialize_mpi(...)` never matched `MPI_Init`, so that pass was skipped
  entirely. Both regexes now also match `(initialize|init|setup|start)_mpi(...)` /
  `(finalize|cleanup|shutdown|stop)_mpi(...)`.
- **Closing-brace anchoring is only valid for a fall-through function.** Added
  `_falls_through(lines, close_brace_line)`, which walks up past blanks/comments/
  preprocessor lines and reports whether the last executable statement terminates
  (`return`/`exit`/`abort`/`throw`/`goto`).

Placement is now: resolve **every** exit of `main()` to the nearest MPI teardown call above
it with no other exit in between (keeping FINI before `MPI_Finalize`, lint rule L3), else to
the exit itself; add a closing-brace anchor only when `_falls_through` is true. The same
fall-through fix applies to regular C functions, where the old `if exits / else close-brace`
split silently dropped the END on a function that both returns early and falls through.
Verified against pristine miniFE `main.cpp`: the tool now picks exactly the two
`finalize_mpi()` lines and no closing-brace anchor.

**Why:** matches the earlier [[bug_annotator_fabricated_report]] lesson — a tool's or
subagent's success report is not evidence.

**How to apply:** after any annotation step, grep the files for the MACROS
(`DFTRACER_CPP_`/`DFTRACER_C_`, NOT bare `DFTRACER` — the inserted
`#include <dftracer/dftracer.h>` is lowercase, so a case-sensitive grep for
`DFTRACER` can read 0 on a file that did get the include) and compare against the
expected function count. Then check `REGION_START`/`REGION_END`/`FINI` balance and
that every `return` in `main` is preceded by END+FINI, with FINI before any
`MPI_Finalize` **or its wrapper** — grep for both. Red flag in a finished trace:
POSIX/STDIO/PAPI present but `CPP_APP` absent or tiny. Detail in
[[dftracer-annotation-lessons]], [[workload-laghos]] and [[workload-minife]].

**Third mode, not a tool bug: template-only headers legitimately yield `functions: 0`.**
Header-only / template-heavy C++ (miniFE, Kokkos/Thrust/RAJA-style) cannot be parsed
standalone, so the clang tools correctly return nothing for those files — but the effect
is identical to a silent failure, since `main.cpp` gets instrumented and the entire solver
does not. Annotate those with an explicit anchor-driven script kept under the session's
`scripts/`, be deliberate about excluding per-element/per-timer-tick helpers, and watch for
overlapping substring anchors (`exchange_externals(` also matches
`begin_exchange_externals(`). Worked example with exclusion list in [[workload-minife]].
