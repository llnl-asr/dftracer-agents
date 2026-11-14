---
name: dftracer_annotate_cpp
description: Annotate C++ files with dftracer C++ macros (DFTRACER_CPP_INIT/FINI, RAII DFTRACER_CPP_FUNCTION, REGION_START/END in main, FUNCTION_UPDATE with comp=) and validate them deterministically. Load for C++ annotation, RAII destructor after FINI, missing REGION_END before a return in main, UPDATE_INT in C++, header-only/template code (.hpp/.ipp), include-order errors in header-only libraries, over-annotated hot loops.
---

# dftracer_annotate_cpp

Converted 2026-11-13 from dftracer-agents: converted from dftracer-agents tool session_annotate_cpp_file (annotations/annotate_cpp.py), skill dftracer-annotate-cpp, C++-specific pitfalls (CP) of dftracer-annotation-lessons, agents dftracer-annotate-cpp and dftracer-validate-cpp. Tools keep their dftracer-agents names, signatures and behavior; the validator dftracer_annotate_cpp_check rules on what the predictors produce.

## Rules

- Predict with session_annotate_cpp_file; validate with dftracer_annotate_cpp_check; on a failed check follow ## Corrector, then predict and validate again.

### From `dftracer-annotate-cpp/SKILL.md § C++ Annotation Rules (dftracer)`

#### C++ Annotation Rules (dftracer)

##### C++ Rule 1 — Include and RAII guard for regular functions

Add the include after all existing `#include` lines in the .cpp file (never in a header):
```cpp
#include <dftracer/dftracer.h>
```

**Regular C++ functions** — use the RAII scope guard (no manual END needed):

```cpp
void my_function(const char *path) {
  DFTRACER_CPP_FUNCTION();                      // RAII — END called automatically when scope exits
  DFTRACER_CPP_FUNCTION_UPDATE("path", path);
  ...
  // No explicit END needed
}
```

- `DFTRACER_CPP_FUNCTION()` must be the **first statement** after the opening `{`.
- No manual END required — the destructor fires on scope exit.
- Never mix C and C++ macros in the same file.
- Never annotate header files (.h, .hpp) — only .cpp / .cxx source files.

##### C++ Rule 2 — REGION macros only in C++ main; RAII everywhere else

**C++ main** — use `DFTRACER_CPP_REGION_START/END` instead of `DFTRACER_CPP_FUNCTION()`,
because the RAII destructor fires after `DFTRACER_CPP_FINI()`, which is wrong:

```cpp
int main(int argc, char **argv) {
  DFTRACER_CPP_INIT(nullptr, nullptr, nullptr);
  DFTRACER_CPP_REGION_START(main_region);       // ← REGION, not FUNCTION
  ...
  if (error) {
    DFTRACER_CPP_REGION_END(main_region);        // ← close region first
    DFTRACER_CPP_FINI();                         // ← then shut down tracer
    return 1;
  }
  ...
  DFTRACER_CPP_REGION_END(main_region);          // ← close region before normal return
  DFTRACER_CPP_FINI();
  return 0;
}
```

- Order at every exit point in main: **REGION_END → FINI → return/exit**.
- The region name (`main_region`) can be any valid C identifier.

##### C++ Rule 3 — Where NOT to use REGION macros

- **C source**: never use `DFTRACER_C_REGION_START` / `DFTRACER_C_REGION_END`.
- **C++ regular functions**: never use `DFTRACER_CPP_REGION_*`. Use `DFTRACER_CPP_FUNCTION()`.
- **C++ main only**: use `DFTRACER_CPP_REGION_START/END`.

##### C++ Rule 4 — UPDATE for I/O metadata

```cpp
void my_write(const char *path, size_t size) {
  DFTRACER_CPP_FUNCTION();
  DFTRACER_CPP_FUNCTION_UPDATE("path", path);   // string params only
  ...
}
```

- Use `DFTRACER_CPP_FUNCTION_UPDATE("name", value)` for string (`const char *`) params.
- Good targets: `path`, `filename`, `name`, `dir`, `mode`.

##### C++ Quick checklist

- [ ] `#include <dftracer/dftracer.h>` added to .cpp files only (never headers)
- [ ] Regular functions: `DFTRACER_CPP_FUNCTION()` as first statement after `{`
- [ ] main: `DFTRACER_CPP_INIT` → `DFTRACER_CPP_REGION_START` → ... → `DFTRACER_CPP_REGION_END` → `DFTRACER_CPP_FINI` → return
- [ ] REGION_END at every exit point in main (including early returns and exit() calls)
- [ ] No REGION macros in non-main functions
- [ ] No C macros (`DFTRACER_C_*`) in C++ files

## Constraints

- C++ files only (.cpp/.cxx/.cc/.C); headers only for template code via the anchor script.
- Never add a C END macro to a RAII function.

## Corner cases

### From `dftracer-annotate-cpp/SKILL.md § Non-standard C++ extensions (`.C`, `.cpp.in`, ...)`

#### Non-standard C++ extensions (`.C`, `.cpp.in`, ...)

`clang_annotate_project` matches extensions CASE-SENSITIVELY and picks the
language per file: `.cpp/.cxx/.cc/.c++` and uppercase `.C/.CC/.CPP/.CXX` are
always C++; `.c` follows `language`. For any other C++ extension pass it
explicitly, e.g. `clang_annotate_project(run_id, language="cpp",
cpp_extensions=[".C", ".cpp.in"])`. Symptom of getting this wrong (Enzo, SW4):
only the handful of `.cpp` files get annotated and `main` in `enzo.C`/`main.C`
never gets INIT/FINI.

### From `dftracer-annotation-lessons/SKILL.md § C++-Specific Pitfalls (CP)`

#### C++-Specific Pitfalls (CP)

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

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector

dftracer_annotate_cpp_check failures and their repairs:
- `compiles` false: fix the reported line; a header-only library whose macros are undefined -> put <dftracer/dftracer.h> first in the entry .cpp (CP9); missing headers -> extra_include_dirs / extra_flags.
- `comp UPDATE first` false: add DFTRACER_CPP_FUNCTION_UPDATE("comp", "<type>") right after DFTRACER_CPP_FUNCTION() (CP7).
- `main uses REGION, not FUNCTION` false: replace FUNCTION() in main with DFTRACER_CPP_REGION_START(main) / REGION_END(main) (C++ Rule 2).
- `REGION_END before every return` false: add REGION_END(main) and FINI before each return in main, before the MPI teardown call or its wrapper (CP8).
- `no END with RAII` / `no UPDATE_INT in C++` false: remove the C END macro; pass numbers as strings (CP5).
- `no dftracer include in a header` false: move the include to the .cpp (CP6), unless the header is template code.
Run the check again; the same failure twice stops for the user.

## Self-heal

- If session_annotate_cpp_file or the check misjudges a C++ construct (templates, lambdas, constructors), update this capability with a fixture for it.

## Self-learn

- What is explored here becomes a capability: see capability_builder. A new annotation failure is a lesson here (LESSONS.md, symptom -> root cause -> fix, dated); a new verified recipe is a WORKED.md entry; a new workload or software met while annotating becomes workload_<x> / software_<x>.

## What works

Verified recipes, dated, as in WORKED.md (full list there):

- [ior/annotate/linking-a-c-app-against-libdftracer-core-so-c-on] 2026-07-06: Link with: LIBS="-ldftracer_core -lstdc++", LDFLAGS+=" -Wl,--allow-shlib-undefined -Wl,--no-as-needed". Runtime/configure-run: prepend the python module lib dir (has GLIBCXX_3.4.29 + libyaml-cpp) AND dftracer/lib64 to LD_LIBRARY_PATH, BEFORE /usr/lib64: export LD_LIBRARY_PATH="$DFT/lib64:/usr/tce/packages/python/python-3.13.2/lib:$LD_LIBRARY_PATH" (resolved: ld.bfd: libdftracer_core.so: undefined reference to std::filesystem::...@GLIBCXX_3.4.26 / ...@CXXABI_1.3.13 then at configure run: "C compiler cannot create exe)
- [ygm/annotate/annotating-a-header-only-c-library-implementatio] 2026-08-04: In the entry .cpp, make #include <dftracer/dftracer.h> the FIRST include, before any of the library's own headers: #include <dftracer/dftracer.h> // must be first #include <ygm/comm.hpp> Also do NOT let clang_annotate_file insert a stray #include <dftracer/dftracer.h> directly into a .ipp/.hpp header (CP6) — it did so once here and had to be manually removed. (resolved: Compile errors deep inside ygm/detail/comm.ipp, collective.hpp, mpi.hpp, and comm_environment.hpp complaining that DFTRACER_CPP_FUNCTION / UPDATE macros are und)
- [ygm-bench/annotate/scaled-4-node-128-rank-traced-run-of-around-the] 2026-08-04: Stripped DFTRACER_CPP_FUNCTION() (leaving a one-line comment explaining why, not silently removed) from the 9 hot-loop functions in include/ygm/detail/comm.ipp: local_progress, local_wait_until, process_receive_queue, flush_next_send, check_completed_sends, check_if_production_halt_required, post_new_irecv, local_process_incoming, handle_completed_send. Kept annotation on the coarser, once-per-logical- operation functions applications actually call directly: async, async_bcast, barrier, all_reduce*, mpi_send/recv/bcast, pack_lambda*, comm_setup, welcome. Coverage check still holds (46 DFTRACER_CPP_FUNCTION() == 46 DFTRACER_CPP_FUNCTION_UPDATE calls after the strip, down from 55/55). Verified the annotated tree still compiles/links cleanly (single MPI runtime, libmpi_gnu_112.so.12 only) after the strip. See CP10 in this skill's SKILL.md for the generalized pattern (any async/polling communication engine, not just YGM) and the diagnostic tell (traced runtime independent of workload size == hot-loop over-annotation, not a real bottleneck). Follow-up idea (not yet implemented): dftracer's own aggregator (src/dftracer/core/aggregator/{aggregator,rules}.cpp) can summarize repeated events into periodic time buckets (e.g. 5s granularity) instead of one event per call -- worth trying as a lighter-weight alternative to fully stripping annotation from hot functions when their behavior is itself diagnostically interesting. (resolved: DFTRACER_ENABLE=0: -n 1000 -> 105.8s, -n 2000 -> 281.75s (roughly proportional). DFTRACER_ENABLE=1 (DATA_DIR=all): -n 2000 and -n 6000 BOTH still running past 5)
- [minife/annotate/template-headers] 2026-08-12: header-only/template code annotated with an anchor-driven, idempotent script kept in the session's scripts/ (DFTRACER_CPP_FUNCTION() + DFTRACER_CPP_FUNCTION_UPDATE("comp", ...) after each opening brace, explicit exclusion list for per-element helpers). (dftracer-annotation-lessons § Template-only projects)
- [ygm/annotate/include-order] CP9: in the entry .cpp, `#include <dftracer/dftracer.h>` first, before the header-only library's own headers (confirmed on llnl/ygm). (dftracer-annotation-lessons)
