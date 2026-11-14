---
name: dftracer_annotate_api
description: dftracer annotation API reference per language (c, cpp, python): init/fini, function annotations, metadata and function-update calls with their signatures and doc URLs; and a check that a snippet uses them correctly. Load when unsure of a dftracer macro name or signature, for 'implicit declaration of DFTRACER_*', 'incompatible integer to pointer conversion' on DFTRACER_C_INIT, mixed C/C++ macros, or Python initialize_log/finalize/log_metadata_event usage.
---

# dftracer_annotate_api

Converted 2026-11-13 from dftracer-agents: converted from dftracer-agents register_annotation_api_tools (dftracer/dftracer_service.py); closure constants _DOCS_BASE and _PYDOCS_BASE hoisted to module level. Tools keep their dftracer-agents names, signatures and behavior; the validator dftracer_annotate_api_check rules on what the predictors produce.

## Rules

- Predict with dftracer_get_init_fini, dftracer_get_function_annotations, dftracer_get_metadata_api, dftracer_get_function_update_api; validate with dftracer_annotate_api_check; on a failed check follow ## Corrector, then predict and validate again.

### From `dftracer-annotate-c/SKILL.md § C Rule 10 (copy)`

#### C Rule 10 — Header include and linker setup

##### 10a — Header placement
Add `#include <dftracer/dftracer.h>` as the **last `#include`** in each `.c`/`.cpp` file.
**Never add it to a `.h` header file.**

##### 10b — Linker flags

**Makefile/autotools:**
```makefile
CFLAGS  += -I$(DFTRACER_PREFIX)/include
LDFLAGS += -L$(DFTRACER_PREFIX)/lib -Wl,-rpath,$(DFTRACER_PREFIX)/lib
LIBS    += -ldftracer_core
```

**CMake:**
```cmake
target_link_libraries(${MY_TARGET} PRIVATE dftracer::dftracer_core)
target_include_directories(${MY_TARGET} PRIVATE ${DFTRACER_PREFIX}/include)
```

**pip/venv install** — library lives inside the Python package tree:
```bash
DFTRACER_SITE=$(python3 -c \
    "import importlib.util, pathlib; \
     p=importlib.util.find_spec('dftracer'); \
     print(pathlib.Path(p.origin).parent)")
DFTRACER_INC="${DFTRACER_SITE}/include"
DFTRACER_LIB="${DFTRACER_SITE}/lib"
# → <venv>/lib/python<ver>/site-packages/dftracer/lib/libdftracer_core.so
```

##### 10c — Transitive deps (if linker reports undefined symbols)
```makefile
LIBS += -ldftracer_core -lcpp-logger -lbrahma
```

##### 10d — Verify build setup before annotating any source
```bash
echo '#include <dftracer/dftracer.h>' >> annotated/src/one_file.c
make -C annotated/src 2>&1 | grep -i "error\|cannot find"
```

##### 10e — Always resolve the dftracer.h include path before clang_syntax_check

`clang_syntax_check` needs `-I<dftracer_include_dir>` or it reports false "file not
found" errors on `#include <dftracer/dftracer.h>` even when the annotation is correct.
Locate the include dir once per session (before the first syntax check, not per-file)
and pass it on every `clang_syntax_check` / `clang_lint_annotations` call:
```bash
DFTRACER_INC=$(python3 -c \
    "import importlib.util, pathlib; \
     p=importlib.util.find_spec('dftracer'); \
     print(pathlib.Path(p.origin).parent / 'include')")
```

## Constraints

- Reference only: these tools never read or write a session.

## Corner cases

### From `dftracer-annotation-lessons/SKILL.md (copy)`

#### DFTRACER_C_INIT third argument is `int*`, never a literal int

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

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector

dftracer_annotate_api_check failures and their repairs:
- `only <lang> API macros` false: replace the unknown name with the one dftracer_get_function_annotations(language) returns.
- `C and C++ macros not mixed` false: use the macro family of the file's language (C++ Rule 1).
- `macro arguments` false: match the signature (INIT 3, UPDATE 2, METADATA 3); keys are string literals; INIT's third argument is NULL/nullptr/&pid.
- Python checks false: `_dft_log = dftracer.initialize_log(...)`, `_dft_log.finalize()`, `_dft_log.log_metadata_event(key, value)`.
Check again; the same failure twice stops for the user.

## Self-heal

- If the API reference is out of date with dftracer's headers, update the dftracer_get_* tool and _API_MACROS together.

## Self-learn

- What is explored here becomes a capability: see capability_builder. A new annotation failure is a lesson here (LESSONS.md, symptom -> root cause -> fix, dated); a new verified recipe is a WORKED.md entry; a new workload or software met while annotating becomes workload_<x> / software_<x>.

## What works

Verified recipes, dated, as in WORKED.md (full list there):

- [c/annotate/init-args] 2026-07-16: DFTRACER_C_INIT(NULL, NULL, NULL) (third argument int*, NULL or &pid); the old default -1 was fatal under Cray clang -std=c99. clang_annotate_file's init_args default changed accordingly. (dftracer-annotation-lessons)
- [tool:dftracer_get_init_fini] returns the exact init/fini signature, placement notes and doc URL per language (c, cpp, python); use it instead of searching the docs.
