---
name: dftracer_annotate_c
description: Annotate C files with dftracer C macros (DFTRACER_C_INIT/FINI, FUNCTION_START/END, UPDATE_STR/INT with comp=) and validate them deterministically. Load for C annotation, 'undeclared identifier' from an UPDATE parameter name, END after return, missing comp=, DFTRACER_C_INIT third argument errors ('incompatible integer to pointer conversion'), dftracer.h not found / linker undefined references to dftracer_core.
---

# dftracer_annotate_c

Converted 2026-11-13 from dftracer-agents: converted from dftracer-agents tool session_annotate_c_file (annotations/annotate_c.py), skill dftracer-annotate-c, C-specific pitfalls (PC) of dftracer-annotation-lessons, agents dftracer-annotate-c and dftracer-validate-c. Tools keep their dftracer-agents names, signatures and behavior; the validator dftracer_annotate_c_check rules on what the predictors produce.

## Rules

- Predict with session_annotate_c_file; validate with dftracer_annotate_c_check; on a failed check follow ## Corrector, then predict and validate again.

### From `dftracer-annotate-c/SKILL.md § C Annotation Rules (dftracer)`

#### C Annotation Rules (dftracer)

> **Before reading the detail rules below, check the `dftracer-cheatsheet` skill** for
> the five critical rules (C1–C5), seven corner cases (CC1–CC7), and the top-10
> known mistakes (M1–M10). The rules below provide the full explanation and examples.

##### C Rule 1 — Include and one START per function, at the very top

Add the include after all existing `#include` lines in the .c file (never in a header):
```c
#include <dftracer/dftracer.h>
```

Then for each selected function:
```c
return_type function_name(params) {
  DFTRACER_C_FUNCTION_START();   // ← FIRST statement after {, nothing before it
  ...
}
```

- **One and only one** START per function body.
- START goes **after** the opening `{`, never before it.
- It must be the **first executable statement** — no variable declarations, no assignments before it.
- **Never** place START inside a control-flow block (`if`, `else`, `for`, `while`,
  `switch`, `do`). Control-flow blocks are NOT functions.
- **Never** annotate: struct/union/enum definitions, typedefs, macro bodies,
  `#ifdef` guard blocks, forward declarations, or function pointer declarations.

##### C Rule 2 — DFTRACER_C_FUNCTION_END before every exit point

```c
return_type function_name(params) {
  DFTRACER_C_FUNCTION_START();
  ...
  if (error) {
    DFTRACER_C_FUNCTION_END();   // ← before early return
    return -1;
  }
  ...
  DFTRACER_C_FUNCTION_END();     // ← before normal return
  return 0;
}
```

- Place END **immediately before every `return` statement** (on its own line, same indent as return).
- For **void functions** that fall off the end (no explicit `return`), place END
  as the **last statement before the closing `}`**:
  ```c
  void helper(int x) {
    DFTRACER_C_FUNCTION_START();
    do_work(x);
    DFTRACER_C_FUNCTION_END();   // ← last statement, void function
  }
  ```
- Every function with a START **must** have a corresponding END at every exit path.
- Never place END after a `return` (it would be unreachable).
- END indentation must match the surrounding block — not at column 0.

##### C Rule 3 — DFTRACER_C_FINI in main before every exit; order around MPI

**Without MPI** — INIT goes right after the opening `{` of main:

```c
int main(int argc, char **argv) {
  DFTRACER_C_INIT(NULL, NULL, NULL);   // ← first, before anything else
  DFTRACER_C_FUNCTION_START();
  ...
  DFTRACER_C_FUNCTION_END();
  DFTRACER_C_FINI();
  return 0;
}
```

**With MPI** — INIT must come AFTER `MPI_Init`/`MPI_Init_thread`, and FINI must
come BEFORE `MPI_Finalize`. dftracer uses MPI internals; initialising before MPI
is ready causes crashes, and finalising after MPI shuts down loses trace data:

```c
int main(int argc, char **argv) {
  MPI_Init(&argc, &argv);              // ← MPI first
  DFTRACER_C_INIT(NULL, NULL, NULL);   // ← dftracer after MPI_Init
  DFTRACER_C_FUNCTION_START();
  ...
  if (bad) {
    DFTRACER_C_FUNCTION_END();         // ← END first
    DFTRACER_C_FINI();                 // ← dftracer FINI before MPI_Finalize
    MPI_Finalize();                    // ← MPI last
    return 1;
  }
  ...
  DFTRACER_C_FUNCTION_END();
  DFTRACER_C_FINI();                   // ← dftracer FINI before MPI_Finalize
  MPI_Finalize();
  return 0;
}
```

- Scan main for `MPI_Init` or `MPI_Init_thread` before placing `DFTRACER_C_INIT`.
  If found, place INIT on the line immediately after the `MPI_Init(...)` call.
- Scan every `return` path and every `MPI_Finalize()` call in main. Place
  `DFTRACER_C_FUNCTION_END()` then `DFTRACER_C_FINI()` immediately **before**
  each `MPI_Finalize()`.
- The full order at every exit from an MPI main:
  `FUNCTION_END → DFTRACER_C_FINI → MPI_Finalize → return`
- Also place FINI before any **process-exit call** (`exit()`, `_exit()`, `abort()`,
  `quick_exit()`) that bypasses `MPI_Finalize`.
- `DFTRACER_C_FINI()` must appear **in the same function as `DFTRACER_C_INIT()` (i.e., main).
- INIT/FINI summary: `MPI_Init → DFTRACER_C_INIT → ... → DFTRACER_C_FINI → MPI_Finalize`

##### C Rule 4 — Classify every annotated function with comp=TYPE

Every annotated function MUST include a `comp` classification UPDATE immediately
after `DFTRACER_C_FUNCTION_START()`. This tags functions in the trace so analysis
tools can group and filter by operation type.

**The four types:**

| Type | When to use | Typical functions |
|------|------------|-------------------|
| `"io"` | File system I/O: open, read, write, close, fsync, stat, delete, rename, getfilesize, mknod | POSIX_Create, POSIX_Xfer, POSIX_Fsync, POSIX_Close, POSIX_Delete, HDF5_Xfer, MMAP_Open |
| `"mem"` | Memory operations: large memcpy, mmap region setup, buffer alloc/free, data copy between memory regions | MMAP_Xfer (memcpy path), any malloc/free of large buffers |
| `"cpu"` | Compute: checksums, compression, encryption, hash, data encoding, format conversion | Any function that transforms data without doing file I/O |
| `"comm"` | Communication: MPI sends/recvs, network I/O, REST API calls, distributed FS ops | MPIIO_Xfer, NCMPI_Xfer, HDFS_Xfer, S3_Xfer, RADOS ops, DFS ops, any MPI_File_* wrapper |

**Usage — always the first UPDATE after START:**

```c
// File I/O function
aiori_fd_t *POSIX_Create(char *testFileName, int flags, aiori_mod_opt_t *param)
{
  DFTRACER_C_FUNCTION_START();
  DFTRACER_C_FUNCTION_UPDATE_STR("comp", "io");            // ← type first
  DFTRACER_C_FUNCTION_UPDATE_STR("filename", testFileName); // then params
  DFTRACER_C_FUNCTION_UPDATE_INT("flags", flags);
  ...
}
```

**Classification guide for common patterns:**
- `Create`, `Open`, `Close`, `Fsync`, `Sync`, `Delete`, `Rename`, `GetFileSize`, `Mknod` → `"io"`
- `Xfer` in POSIX, MMAP, HDF5, NCMPI where data goes to/from a file → `"io"`
- `Xfer` in MPIIO, S3, HDFS, RADOS, DFS, CEPHFS where a network/RPC call is made → `"comm"`
- `Xfer` in MMAP where the transfer is a `memcpy` into the mmap region → `"mem"`
- `init`, `final`, `initialize`, `finalize` for any backend → `"io"` (I/O stack lifecycle)
- Functions doing checksums, compression, encryption, hashing → `"cpu"`

##### C Rule 5 — Track important I/O metadata with FUNCTION_UPDATE (after comp=TYPE)

```c
ssize_t my_read(const char *filename, void *buf, size_t count, off_t offset) {
  DFTRACER_C_FUNCTION_START();
  DFTRACER_C_FUNCTION_UPDATE_STR("filename", filename);   // string params
  DFTRACER_C_FUNCTION_UPDATE_INT("count",    (int)count); // numeric params
  DFTRACER_C_FUNCTION_UPDATE_INT("offset",   (int)offset);
  ...
  DFTRACER_C_FUNCTION_END();
  return result;
}
```

- **String params** (`const char *`): use `DFTRACER_C_FUNCTION_UPDATE_STR("name", ptr)`
- **Numeric params** (`size_t`, `off_t`, `int`, `long`): use `DFTRACER_C_FUNCTION_UPDATE_INT("name", (int)val)`
- The variable name in the UPDATE call **must exactly match** the parameter name in
  the function definition — otherwise you get `undeclared identifier` compile errors.
- **Opaque handle typedefs** (`MPI_File`, `hid_t`, `ncid`, `hsize_t`) use `UPDATE_INT` with `(int)` cast.

##### C Rule 5 — Error-checking macros that embed early exits

```c
ssize_t my_write(...) {
  DFTRACER_C_FUNCTION_START();
  ...
  DFTRACER_C_FUNCTION_END();          // ← before macro that may return/goto
  NCMPI_CHECK(ncmpi_put_vara(...), "write failed");
  ...
  DFTRACER_C_FUNCTION_END();          // ← before normal return
  return 0;
err:
  DFTRACER_C_FUNCTION_END();          // ← before goto-target fallthrough
  return -1;
}
```

For **`goto`-based error handling** (HDF5):
```c
ssize_t hdf5_write(...) {
  DFTRACER_C_FUNCTION_START();
  ...
  if ((fid = H5Fcreate(...)) < 0) goto done;
  ...
done:
  DFTRACER_C_FUNCTION_END();   // ← place END at the goto label, not before each goto
  return ret;
}
```

##### C Rule 7 — Forward declarations vs definitions: only annotate definitions

```c
// ❌ DO NOT annotate — forward declaration (no body, no param names)
static IOR_offset_t POSIX_Xfer(int, aiori_fd_t *, IOR_size_t *,
                               IOR_offset_t, IOR_offset_t, aiori_mod_opt_t *);

// ✅ Annotate this one — definition with named params and body
static IOR_offset_t POSIX_Xfer(int access, aiori_fd_t *file, IOR_size_t *buffer,
                               IOR_offset_t length, IOR_offset_t offset,
                               aiori_mod_opt_t *param)
{
  DFTRACER_C_FUNCTION_START();
  ...
}
```

Quick filter to find only definitions: `grep -n "FUNCTION_NAME" file.c | grep -v ";$"`

##### C Rule 8 — Vendor-specific filesystem functions are I/O functions

| Prefix | Filesystem | Examples |
|--------|-----------|---------|
| `gpfs_*` | IBM GPFS/Spectrum Scale | `gpfs_free_all_locks`, `gpfs_access_start` |
| `beegfs_*` | BeeGFS | `beegfs_getStriping`, `beegfs_createFilePath` |
| `lustre_*` | Lustre | `lustre_disable_file_locks` |
| `hdfs_*` | Hadoop HDFS | Any HDFS JNI wrapper |
| `ceph_*` | Ceph | Any libcephfs wrapper |
| `daos_*` | DAOS | Any DAOS API wrapper |

##### C Rule 9 — Coverage verification: scan all definitions after each file

```bash
# Check 1 — list every annotated function
awk '/^[a-zA-Z].*\(/ {func=$0} /DFTRACER_C_FUNCTION_START/ {print NR": "func}' \
    annotated/src/foo.c

# Check 2 — verify comp=TYPE present in every annotated function
grep -c "DFTRACER_C_FUNCTION_START"          annotated/src/foo.c
grep -c 'DFTRACER_C_FUNCTION_UPDATE_STR.*comp' annotated/src/foo.c
# Both counts must be equal
```

##### C Rule 10 — Header include and linker setup

###### 10a — Header placement
Add `#include <dftracer/dftracer.h>` as the **last `#include`** in each `.c`/`.cpp` file.
**Never add it to a `.h` header file.**

###### 10b — Linker flags

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

###### 10c — Transitive deps (if linker reports undefined symbols)
```makefile
LIBS += -ldftracer_core -lcpp-logger -lbrahma
```

###### 10d — Verify build setup before annotating any source
```bash
echo '#include <dftracer/dftracer.h>' >> annotated/src/one_file.c
make -C annotated/src 2>&1 | grep -i "error\|cannot find"
```

###### 10e — Always resolve the dftracer.h include path before clang_syntax_check

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

##### C Quick checklist

- [ ] **Build setup verified before first annotation** (Rule 10d)
- [ ] `#include <dftracer/dftracer.h>` added to every annotated `.c`/`.cpp` — last include, never in `.h`
- [ ] Linker flags: `-ldftracer_core -L<prefix>/lib -Wl,-rpath,<prefix>/lib`
- [ ] ALL non-trivial functions annotated — skip only pure getters/setters/formatters
- [ ] START is the first statement after `{` (not before `{`)
- [ ] START is in the definition body, not a forward declaration (Rule 7)
- [ ] `DFTRACER_C_FUNCTION_UPDATE_STR("comp", "<type>")` is FIRST UPDATE after every START
- [ ] `comp` type is one of: `"io"`, `"mem"`, `"cpu"`, `"comm"`
- [ ] Parameter names in UPDATE match the definition, not the declaration
- [ ] END placed before every VISIBLE `return` (same indentation level)
- [ ] Error-checking macros (MPI_CHECK, NCMPI_CHECK): END before them if they wrap a return
- [ ] goto-based error handling: single END at the label, not before each goto
- [ ] Void functions: END placed as last statement before `}`
- [ ] END indentation is not at column 0
- [ ] main: MPI_Init (if present) → DFTRACER_C_INIT → ... → DFTRACER_C_FINI → MPI_Finalize → return
- [ ] Vendor-specific functions (gpfs_*, beegfs_*, lustre_*) annotated as comp="io"
- [ ] Coverage verified: START count == comp count; no missed functions

## Constraints

- C files only (.c); .C is C++. Never mix C and C++ macros in one file.
- Never annotate headers or forward declarations (R4, R5).

## Corner cases

### From `dftracer-annotation-lessons/SKILL.md § C-Specific Pitfalls (PC)`

#### C-Specific Pitfalls (PC)

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

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector

dftracer_annotate_c_check failures and their repairs:
- `compiles` false: read the first error; an UPDATE naming a declaration-only parameter -> use the definition's parameter name (C Rule 5); a macro inside a #define, comment or string -> move it (Corner cases); a missing system header -> pass extra_include_dirs / extra_flags (-DHAVE_CONFIG_H).
- `comp UPDATE first` false: add DFTRACER_C_FUNCTION_UPDATE_STR("comp", "<io|mem|cpu|comm>") right after the START (C Rule 4).
- `END before every exit` false: add END before each visible return; braceless returns need braces first (clang_add_braces); error macros that hide returns (MPI_CHECK) get no END (C Rule 5, error-checking macros).
- `main` / `project` init-fini false: DFTRACER_C_INIT(NULL, NULL, NULL) first in main and DFTRACER_C_FINI() before every exit, before MPI_Finalize (C Rule 3).
- `INIT third argument is a pointer` false: NULL or &pid, never -1.
- `critical I/O calls annotated` false: annotate those functions (internal helpers too, Rule F).
Then run the check again; the same failure twice stops for the user.

## Self-heal

- If session_annotate_c_file's recipe or the check is wrong, update this capability (capability_update) with the case as a lesson.

## Self-learn

- What is explored here becomes a capability: see capability_builder. A new annotation failure is a lesson here (LESSONS.md, symptom -> root cause -> fix, dated); a new verified recipe is a WORKED.md entry; a new workload or software met while annotating becomes workload_<x> / software_<x>.

## What works

Verified recipes, dated, as in WORKED.md (full list there):

- [ior/annotate/inserting-dftracer-c-function-end-into-braceless] 2026-06-17: Grep for braceless early-exit lines before annotating each file: grep -n "if.*return\|if.*continue\|if.*break" <file.c> | grep -v "{" | grep -v "//" For each hit, add explicit braces FIRST, then insert END: // Before: if (dryRun) return NULL; // After: if (dryRun) { DFTRACER_C_FUNCTION_END(); return NULL; } (resolved: Inserting END made the early return unconditional — dryRun check bypassed. Or: compiler error "expected ';' before DFTRACER_C_FUNCTION_END")
- [ior/annotate/dftracer-c-fini-placed-before-ior-main-call-all] 2026-06-17: FINI must appear AFTER the top-level benchmark function returns. Structure: int main(...) { MPI_Init(...); DFTRACER_C_INIT(NULL, NULL, NULL); DFTRACER_C_FUNCTION_START(); ... ior_main(opts); // ← real I/O happens here DFTRACER_C_FUNCTION_END(); DFTRACER_C_FINI(); // ← AFTER ior_main, not before the benchmark call MPI_Finalize(); return 0; } Before placing FINI, identify the "benchmark call" in main() — the call that does all the real work — and ensure FINI comes after it returns. (resolved: Trace contains only the main() span. No POSIX_Create, MPIIO_Open, HDF5_Close or any other backend spans appear even though those functions are annotated.)
- [ior/annotate/hdf5-stray-end-inserted-because-grep-matched-for] 2026-06-17: Always filter grep results to definitions only: grep -n "HDF5_Create" file.c | grep -v ";$" The definition has a body ({...}); the forward declaration ends with ';'. Annotate ONLY the definition line, never the declaration. (resolved: HDF5_Create had a DFTRACER_C_FUNCTION_END() inserted before DFTRACER_C_FUNCTION_START() — compile error or incorrect trace span.)
- [ior/annotate/annotating-ior-c-on-tuolumne-dftracer-c-init-thi] 2026-07-06: Always pass init_args="NULL, NULL, NULL" to clang_annotate_project / clang_annotate_file for C. All three DFTRACER_C_INIT args are pointers. (resolved: annotated/src/ior.c:110:37: error: incompatible integer to pointer conversion passing 'int' to parameter of type 'int *' [-Wint-conversion] DFTRACER_C_INIT(NULL)
- [ior/annotate/validate-annotations-c-false-positive-audit-afte] 2026-09-10: Rewrote the check in annotation_validate.py: - `_C_CRITICAL_CALL_RE`: word-bounded regex requiring the identifier be followed by `(` (real call syntax), so `is_open`/`obj_read`/`.open = fn`/function-name substrings no longer match. - `_strip_c_comment()`: blanks out // and /* */ comment text per line (stateful across lines for block comments) before matching, so comment-text hits are eliminated. - Brace-depth tracking: `cur_fn` is now cleared as soon as depth returns to 0 after the function's own opening line, not just when the next fn_re match appears -- fixes the two misattribution cases. Verified directly (not just by re-running the MCP tool, which is stale in this stdio session): imported the patched `_validate_c_like` and ran it against all 7 previously- flagged files in workspaces/ior/20260911_033625/annotated/src/ -- 0 findings, down from 14. Fix confirmed correct; takes effect starting the next MCP session. (resolved: validate_annotations reported 14 "critical I/O flow not annotated" / lint findings in src/ that were all, on manual inspection, false positives: matches inside )
- [ior/annotate/coverage] C coverage check after every file: START count == `UPDATE_STR("comp"` count, every definition with a syscall annotated (awk scan); dftracer_annotate_c_check does both. (dftracer-cheatsheet, coverage commands)
