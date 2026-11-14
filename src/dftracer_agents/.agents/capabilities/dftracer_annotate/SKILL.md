---
name: dftracer_annotate
description: dftracer annotation stage (pipeline stage 2): scope, dispatch per language (C, C++, Python), emit app-parameter metadata and gate the annotated tree before the build. Load for any 'annotate <app> with dftracer' request, for validate_annotations failures ('critical I/O flow not annotated', 'no dftracer init found anywhere', 'no dftracer finalize found', 'no app-parameter metadata events'), empty traces after annotation, START/comp count mismatch, or when deciding which annotation rules apply.
---

# dftracer_annotate

Converted 2026-11-13 from dftracer-agents: converted from dftracer-agents tools annotate_add_app_metadata and validate_annotations (session/annotation_validate.py), skills dftracer-annotate-general, dftracer-annotate-process, dftracer-annotate-project, dftracer-cheatsheet, dftracer-annotation-lessons (standing rules, core rules, general pitfalls, log), dftracer-lessons (copy), agent dftracer-annotator (.yaml + .md). Tools keep their dftracer-agents names, signatures and behavior; the validator dftracer_annotate_check rules on what the predictors produce.

## Rules

- Predict with annotate_add_app_metadata, validate_annotations; validate with dftracer_annotate_check; on a failed check follow ## Corrector, then predict and validate again.

### From `dftracer-annotate-general/SKILL.md § C / C++ / Python Annotation Rules (dftracer)`

#### C / C++ / Python Annotation Rules (dftracer)

These rules apply whenever you manually annotate C, C++, or Python source files.
Violating any rule will cause build failures or missing trace data.

##### Rule D — Declare dftracer as a DIRECT dependency of the app's own build manifest (MANDATORY, all languages)

Annotating an app's source is not the whole integration — the app's own build
manifest must also declare dftracer as a direct dependency, so a plain build
of the annotated tree pulls dftracer in automatically instead of relying on a
side-by-side session install the app's manifest knows nothing about. This is
what makes the integration a durable, patchable change rather than a one-off
session artifact — a diff against the app's manifest is exactly the kind of
patch this project's self-learning/patch workflow expects to produce and
re-apply later.

Add the dependency in whichever manifest the app's build system reads (see
the matching `software-<tool>` skill for exact mechanics):
- **Python (`pyproject.toml`)** → add `dftracer` (and `pydftracer` if not
  pulled in transitively — verify, see [[tools-pydftracer]]) to
  `[project].dependencies` or an appropriate `[project.optional-dependencies]`
  group. See [[software-pip]].
- **Meson (`meson.build`)** → add a `dependency('dftracer', ...)` call for the
  relevant target — NOTE: as of this writing dftracer ships no `.pc`
  pkg-config file (only CMake config files), so plain `dependency('dftracer')`
  will not resolve; until that's fixed upstream, document the CFLAGS/LDFLAGS
  injection as the patch instead (see [[software-meson]]) and revisit adding
  a real `dependency()` call once a `.pc` file exists.
- **CMake (`CMakeLists.txt`)** → add `find_package(dftracer CONFIG REQUIRED)`
  (or the project's existing dependency-list convention) plus
  `target_link_libraries(<target> dftracer::dftracer_core)` or equivalent. See
  [[software-cmake]].
- **Autotools (`configure.ac`)** → add a `PKG_CHECK_MODULES`/`AC_CHECK_LIB`
  probe for dftracer alongside the project's other dependency checks. See
  [[software-autotools]].

This patch belongs in the ANNOTATED tree (or as a tracked patch file under the
run's `patches/` directory per this project's run-scoped layout), not just in
session state — the whole point is that re-checking out the annotated source
and building it fresh, without any session-specific env setup, should already
know to pull in dftracer.

##### Rule 0 — Only annotate functions worth tracing (skip trivial ones)

**Annotate a function if it:**
- Performs file or network I/O (`open`, `read`, `write`, `close`, `fread`, `fwrite`,
  `mmap`, `send`, `recv`, `MPI_File_*`, `MPI_Send`, `MPI_Recv`, …)
- Moves or transforms significant data (memcpy, checksums, compression, serialize)
- Allocates or frees large buffers (`malloc`/`free` of buffers > a few KB)
- Contains a loop that iterates over data (segments, blocks, ranks)
- Is a top-level driver or orchestration function (`main`, `run_test`, `benchmark_*`)
- Takes measurable wall time (anything you would profile with perf or gprof)

**Skip a function if it:**
- Is a simple getter or setter (returns a field, sets one value, ≤ 5 lines)
- Only does arithmetic or string formatting with no I/O
- Is a small utility called in a tight inner loop (would add overhead per iteration)
- Is a constructor/destructor with no I/O side effects
- Is already wrapped by a larger annotated function that covers the same work
  (Python pure-orchestration call chains: see `dftracer-annotate-python` Rule 9
  for the full pattern and the distinction from Rule A below)

**Always annotate, even if they look small:**
- Backend lifecycle functions (`*_init`, `*_final`, `*_initialize`, `*_finalize`,
  `*_open_backend`, `*_close_backend`) — **Never apply Rule 0 to lifecycle functions.**
- Sync/flush functions (`*_Fsync`, `*_Sync`, `*_Flush`)
- Delete/rename/stat functions (`*_Delete`, `*_Rename`, `*_Mknod`, `*_GetFileSize`)
- **Vendor-specific filesystem helpers** (`gpfs_*`, `beegfs_*`, `lustre_*`, `hdfs_*`,
  `ceph_*`, `daos_*`) — annotate as `comp="io"`

##### Rule F — Scan the entire file for internal helpers

After annotating the public API, always scan the **whole file** for unannotated helpers.

**Syscall triggers — if a function body contains any of these, it is an I/O function:**

| Trigger call | Category |
|-------------|----------|
| `open()`, `read()`, `write()`, `close()`, `stat()` | POSIX syscall |
| `mknod()`, `mkstemp()`, `unlink()`, `rename()` | File lifecycle |
| `ioctl()`, `fcntl()` | Kernel I/O control |
| `gpfs_fcntl()`, `beegfs_getStripeInfo()`, `llapi_*()` | Vendor FS |
| `cuFileHandleRegister()`, `cuFileRead()`, `cuFileWrite()` | GPU Direct |
| `MPI_File_*()` | MPI-IO |

**Workflow:**
```bash
# Step 1 — list definitions
grep -n "^[a-zA-Z_].*(.*)$" src/foo.c | grep -v ";"

# Step 2 — cross-reference against annotated list
awk '/^[a-zA-Z_].*\(/ {func=NR": "$0} /DFTRACER_C_FUNCTION_START/ {print func}' annotated/src/foo.c

# Step 3 — for any definition not in Step 2, check body for syscall triggers
```

##### Rule D — Complete coverage: annotate everything, skip only trivial functions

**The default is to annotate. Skip requires justification.**

**Permanent skip conditions (Rule 0):**
- Getter or setter returning a single field, ≤ 5 lines, no I/O
- Pure arithmetic, string formatting, or logging with no data movement
- Small utility called inside a tight inner loop

**After annotating a file**, verify coverage:
```bash
grep -c "^[a-zA-Z].*(.*)$\|^[a-zA-Z].* (\*" src/foo.c  # rough definition count
grep -c "DFTRACER_C_FUNCTION_START" annotated/src/foo.c   # START count
```

##### Rule G — Verify the write, not just the response; grep is defense-in-depth

`clang_annotate_file` now writes its result **to disk immediately by
default** (`write_immediately=True`, the default — do not pass
`write_immediately=False` unless you are deliberately doing a multi-step
add-braces → annotate → syntax-check → write compose pipeline). A single
call is sufficient: the response includes `written_to_disk: true`,
`bytes_written`, and `disk_mtime_ns` confirming real bytes landed on disk.
This is the **primary, structural fix** for the failure mode where an agent
called the tool, got a plausible "N insertions" response, and reported full
success to the orchestrator without anything ever being written to disk
(because the old version only mutated an in-memory cache and required a
separate, easily-forgotten `clang_write_annotated_file` call). That failure
mode is now closed at the tool level, not by agent discipline.

Two things remain worth checking as defense-in-depth, not as the primary
safeguard:

1. **Check `written_to_disk` in the response.** If it is `false`, you (or a
   prior step) explicitly passed `write_immediately=False` — you MUST still
   call `clang_write_annotated_file` before moving on, or the change is lost.
2. **`already_annotated: true` ground-truthing.** `clang_annotate_file` keeps
   an in-memory file cache (keyed by `run_id` + relative path) so multiple
   tool calls can collaborate on one file without extra disk round-trips. The
   cache is invalidated whenever the file's on-disk mtime changes underneath
   it, but there is one situation this cannot fully protect against: if a
   source tree is **deleted and re-copied** mid-session at the same path fast
   enough, or via a path the MCP process never directly `stat()`s between
   calls, a stale cache entry can still cause the tool to report
   `already_annotated: true` (0 insertions) even though the file on disk
   currently has **zero** dftracer macros. This happened during an h5bench
   session where the source tree was reset mid-pipeline — the tool silently
   no-op'd an entire annotation stage.

   After ANY response that reports `already_annotated: true` (or a
   suspiciously low insertion count), ground-truth it before trusting it:

   ```bash
   # C — expect at least one hit per already-annotated file
   grep -rl "DFTRACER_C_INIT" annotated/

   # C++
   grep -rl "DFTRACER_CPP_INIT\|DFTRACER_CPP_FUNCTION" annotated/

   # Python
   grep -rl "DFTRACER_PY_INIT\|dftracer" annotated/*.py
   ```

   If grep finds **zero** matches despite `already_annotated: true`, the
   cache is stale — re-run the annotation call. This is especially important
   immediately after any source tree reset/re-copy/re-clone mid-session (e.g.
   after a build failure recovery that wipes `source/` or `annotated/` and
   repopulates it from scratch).

##### Rule E — Error paths: END only before visible return/throw

```c
// ✅ Correct — END before the explicit return you can see
int my_fn(const char *path) {
  DFTRACER_C_FUNCTION_START();
  int fd = open(path, O_RDONLY);
  if (fd < 0) {
    DFTRACER_C_FUNCTION_END();   // ← explicit return you can see
    return -1;
  }
  DFTRACER_C_FUNCTION_END();
  return 0;
}

// ✅ Correct — don't add END before macro; the macro's internal return is invisible
int mpi_fn(MPI_File *fh, const char *path) {
  DFTRACER_C_FUNCTION_START();
  MPI_CHECK(MPI_File_open(...), "open failed");  // ← no END here
  DFTRACER_C_FUNCTION_END();
  return 0;
}

// ✅ Correct — goto pattern: single END at the label covers all paths
int hdf5_fn(const char *path) {
  DFTRACER_C_FUNCTION_START();
  if ((fid = H5Fopen(...)) < 0) goto done;
done:
  DFTRACER_C_FUNCTION_END();   // ← one END at label covers all goto paths
  return ret;
}
```

##### Rule A — Annotate at every abstraction layer (wrapper calls)

When a higher-level function delegates to a lower-level annotated function, annotate BOTH:
```
MMAP_Xfer()      ← annotate (captures MMAP-level intent)
  └─ POSIX_Xfer() ← also annotated (captures syscall-level detail)
```

##### Rule B — Annotation order within a file: simplest functions first

1. Functions with a **single return statement**
2. Functions with **2–3 returns** in shallow control flow
3. Functions with **multiple returns in nested blocks**
4. Functions with **error-checking macros** that may hide exits

##### Rule C — Backend annotation priority order

1. **Reference / POSIX backend** — simplest, establishes the pattern
2. **Wrapper backends** (MMAP, AIO) — wrap POSIX
3. **MPI-based backends** (MPIIO, NCMPI, parallel HDF5)
4. **High-level library backends** (HDF5, NetCDF)
5. **Distributed / cloud backends** (HDFS, DFS, S3, CEPHFS, RADOS)
6. **Specialized / research backends** (PMDK, IME, Gfarm, CHFS, aio)

Build and run a smoke test for each backend immediately after annotating it.

##### Fortran Programs — No C main() available

Fortran codes (e.g. Flash-X, many HPC multiphysics codes) have a Fortran `program`
entry point, not a C `main()`. This means DFTRACER_C_INIT/DFTRACER_C_FINI cannot be
placed in the application source.

**Solution for FUNCTION/HYBRID mode:**
Create a separate C wrapper file with constructor/destructor attributes:

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

Compile to `.o` and link it into the final binary (e.g. add to `ALL_OBJ_FILES` in
GNU Make, or to the link line). The constructor runs before `program` starts; the
destructor runs after exit.

**Fallback:** Some Fortran linkers (observed: CCE `crayftn`) do not reliably fire
constructor attributes. If FUNCTION mode produces empty traces despite the wrapper
being linked, **pivot to PRELOAD mode** — it captures HDF5/POSIX/MPI I/O at the
library level without requiring INIT/FINI in the application. See
[[dftracer-preload-run]] and [[dftracer-annotation-lessons]] PF1.

##### Quick checklist before writing annotated code

- [ ] Language-specific include / import added
- [ ] Only functions with I/O, data movement, or measurable runtime are annotated (Rule 0)
- [ ] Backend lifecycle functions always included
- [ ] Vendor filesystem helpers (`gpfs_*`, `beegfs_*`, `lustre_*`) included as `comp="io"`
- [ ] Wrapper functions annotated even if the underlying call is also annotated (Rule A)
- [ ] Annotation order: single-return functions first (Rule B)
- [ ] No annotation inside header files, macro bodies, or forward declarations
- [ ] For duplicate function names: annotated only the definition (Rule 7)
- [ ] After each file: verify definition count ≈ START count (Rule 9)
- [ ] Build passes after each file — fix errors before moving to the next file
- [ ] **Fortran programs:** constructor/destructor wrapper created and linked (or PRELOAD mode selected)

### From `dftracer-annotate-process/SKILL.md § Manual Annotation Process (gradual, iterative)`

#### Manual Annotation Process (gradual, iterative)

`session_annotate_source` returns a **plan** — it does NOT write files.
Annotate manually using `session_read_file` + `session_write_file`.

---

### From `dftracer-annotate-process/SKILL.md § Coverage Requirement`

#### Coverage Requirement

**All storage backend functions MUST be annotated. Skipping due to difficulty is not acceptable.**

A function may only be permanently skipped if it qualifies under **Rule 0**
(trivial — getter/setter, ≤5 lines, no I/O, no data movement). Every other
function, regardless of complexity, MUST eventually be annotated.

**Storage backends that always require full annotation:**
- `aiori-POSIX.c` — POSIX_Create, POSIX_Open, POSIX_Xfer, POSIX_Close, POSIX_Delete,
  POSIX_Rename, POSIX_Fsync, POSIX_Sync, POSIX_GetFileSize, POSIX_Mknod
- `aiori-MPIIO.c` — MPIIO_Open, MPIIO_Xfer, MPIIO_Close, MPIIO_Delete, MPIIO_Fsync,
  MPIIO_GetFileSize
- `aiori-HDF5.c`, `aiori-NCMPI.c`, `aiori-DFS.c`, `aiori-S3*.c` — all I/O functions
- Any other `aiori-*.c` file — treat every non-trivial function as mandatory
- **Lifecycle functions always included:** `*_init`, `*_final`, `*_initialize`, `*_finalize`,
  `*_Sync`, `*_Fsync`, `*_Delete`, `*_Rename` — even if the body is short

**Mandatory POSIX backend function list:**
- `POSIX_Create`, `POSIX_Open`, `POSIX_Xfer`, `POSIX_Close`, `POSIX_Delete`
- `POSIX_Fsync`, `POSIX_Sync`, `POSIX_GetFileSize`, `POSIX_Mknod` (even if "deprecated")
- `POSIX_Rename`, `POSIX_Initialize`, `POSIX_Finalize`
- `gpfs_free_all_locks`, `gpfs_access_start`, `gpfs_access_end`, `gpfs_fineGrainWriteSharing`, `gpfs_fineGrainReadSharing`
- `beegfs_getStriping`, `beegfs_compatibleFileExists`, `beegfs_createFilePath`, `beegfs_isOptionSet`
- `lustre_disable_file_locks`

**Backend annotation order:**
1. POSIX / simplest reference backend
2. Wrapper backends (MMAP, AIO)
3. MPI-based backends (MPIIO, NCMPI)
4. High-level library backends (HDF5, NetCDF)
5. Distributed / cloud (HDFS, DFS, S3, CEPHFS, RADOS)
6. Specialized / research backends last

---

### From `dftracer-annotate-process/SKILL.md § annotation_logs/ Folder (mandatory — create before Pass 1)`

#### annotation_logs/ Folder (mandatory — create before Pass 1)

```
workspaces/<run_id>/annotation_logs/
  annotation_process.log   ← running log of every action taken (append-only)
  annotation_status.md     ← per-function status table (update after every function)
  annotation_report.md     ← final summary (written after all passes complete)
```

##### annotation_process.log format

```
[PASS1] src/ior.c — added INIT/FINI/START/END — build: SUCCESS
[PASS2] src/aiori-POSIX.c — POSIX_Create — START+END×2+UPDATE×2 — build: SUCCESS
[PASS2] src/aiori-MPIIO.c — MPIIO_Xfer — attempt 1 FAILED: data_fn undeclared
[PASS2] src/aiori-MPIIO.c — MPIIO_Xfer — attempt 2 — fixed END order — build: SUCCESS
[SKIP]  src/aiori-POSIX.c — POSIX_options — Rule 0: trivial, no I/O
[ERROR] src/aiori-MPIIO.c — MPIIO_Open — FAILED 2× — kept include-only — PENDING RETRY
```

##### annotation_status.md format

```markdown
# Annotation Status — <run_id>

## Storage Backends (mandatory coverage)

| File | Function | Status | comp | Traced in Test | Attempts | Notes |
|------|----------|--------|------|----------------|----------|-------|
| aiori-POSIX.c | POSIX_Create | ✅ DONE | io | ✅ 2 events | 1 | START+END×2+UPDATE |
| aiori-POSIX.c | gpfs_access_start | ✅ DONE | io | ⚠️ no GPFS | 1 | #ifdef guarded |
| aiori-MPIIO.c | MPIIO_Open | ⚠️ PENDING | — | — | 2 | complex control flow |

## Skipped (Rule 0 — permanent)

| File | Function | Reason |
|------|----------|--------|
| aiori-POSIX.c | POSIX_options | Trivial option helper, no I/O |
```

Status values:
- `✅ DONE` — annotated and build passes
- `⚠️ PENDING` — not yet annotated or annotation failed; MUST be retried
- `❌ INCLUDE-ONLY` — only `#include` added; annotation failed and was reset; MUST retry
- `⏭️ SKIPPED (Rule 0)` — permanently exempt

---

### From `dftracer-annotate-process/SKILL.md § Build Prep — BEFORE Pass 1 (Makefile patching)`

#### Build Prep — BEFORE Pass 1 (Makefile patching)

```bash
INSTALL_ANN=<workspace>/install_ann

# Patch src/Makefile (not just the top-level)
sed -i "s|^CFLAGS = |CFLAGS = -I${INSTALL_ANN}/include |" annotated/src/Makefile
sed -i "s|^LIBS = |LIBS = -ldftracer_core -lcpp-logger -lbrahma |" annotated/src/Makefile
sed -i "s|^LDFLAGS = |LDFLAGS = -L${INSTALL_ANN}/lib -Wl,-rpath,${INSTALL_ANN}/lib |" annotated/src/Makefile

# Clean stale dependency files — ALWAYS after a Makefile patch
rm -rf annotated/.deps annotated/src/.deps && make -C annotated clean
```

---

### From `dftracer-annotate-process/SKILL.md § Pass 1 — INIT and FINI in main (entry files only)`

#### Pass 1 — INIT and FINI in main (entry files only)

1. Read the file with `session_read_file`
2. Add `#include <dftracer/dftracer.h>` after the last existing `#include`
3. Check for `MPI_Init` or `MPI_Init_thread`:
   - **No MPI**: `DFTRACER_C_INIT(NULL, NULL, NULL);` as first statement after `{` of main
   - **Has MPI**: `DFTRACER_C_INIT(NULL, NULL, NULL);` immediately AFTER the `MPI_Init(...)` call
4. Before every `return` in main AND before any `exit()`/`abort()`/`MPI_Abort()`:
   - C (no MPI): `DFTRACER_C_FINI();`
   - C (with MPI): `DFTRACER_C_FINI();` then `MPI_Finalize();` then `return`
5. **If code has explicit DFTRACER_C_INIT(), do NOT set DFTRACER_INIT=1 in the env**
6. Write the file, build, fix errors, log to `annotation_process.log`.

---

### From `dftracer-annotate-process/SKILL.md § Pass 2 — FUNCTION_START and FUNCTION_END`

#### Pass 2 — FUNCTION_START and FUNCTION_END

For each backend file, work through ALL I/O functions — start with the simplest:

1. Read the file.
2. For each function: apply Rule D (skip only pure getters/setters ≤5 lines with no I/O).
3. Determine `comp` type BEFORE writing any macro: `"io"`, `"mem"`, `"cpu"`, or `"comm"`.
4. Annotate the function:
   a. Count VISIBLE `return` statements: `grep -n "return" <function_lines>`
   b. Identify multi-line signatures by scanning upward from `{`
   c. Insert `DFTRACER_C_FUNCTION_START();` as FIRST statement after `{`
   d. Insert `DFTRACER_C_FUNCTION_UPDATE_STR("comp", "<type>");` immediately after START
   e. Insert `DFTRACER_C_FUNCTION_END();` before every VISIBLE `return`
   f. For goto-based error handling: single END at the goto label
   g. For void functions: END as last statement before `}`
5. Write the file, run `rm -rf .deps src/.deps` if Makefile was touched, then build.
6. **On failure**: do NOT reset and skip. Fix the specific macro. Only after 3 attempts should you reset to include-only.
7. Log every attempt to `annotation_process.log`.
8. Update `annotation_status.md` (including `comp` column) after every function.
9. **After every file** — run coverage verification:
   ```bash
   grep -c "DFTRACER_C_FUNCTION_START"               annotated/src/foo.c
   grep -c 'DFTRACER_C_FUNCTION_UPDATE_STR.*"comp"'  annotated/src/foo.c
   # Counts must be equal
   ```

---

### From `dftracer-annotate-process/SKILL.md § Pass 3 — FUNCTION_UPDATE for I/O metadata`

#### Pass 3 — FUNCTION_UPDATE for I/O metadata

For functions with meaningful I/O parameters:
- `DFTRACER_C_FUNCTION_UPDATE_STR("name", param)` for `const char *` params
- `DFTRACER_C_FUNCTION_UPDATE_INT("name", (int)param)` for numeric params
- Always read parameter names from the **definition**, not a forward declaration.

---

### From `dftracer-annotate-process/SKILL.md § Pass 4 — Retry PENDING / INCLUDE-ONLY functions`

#### Pass 4 — Retry PENDING / INCLUDE-ONLY functions

1. Read the function carefully — treat it as a fresh annotation.
2. For complex control flow (switch/case, nested MPI error checks, multi-return):
   - List every exit path before writing any macro
   - For `switch`: place END before each `case`'s `break` or `return`
3. Build after each retry.
4. If it passes: update status to `✅ DONE`.
5. If it fails again: ask the user for guidance, then add a pitfall entry.

---

### From `dftracer-annotate-process/SKILL.md § Key safety rules for every pass`

#### Key safety rules for every pass

- Verify line count: annotated file must have MORE lines than original
- Work one function at a time — build after every function in complex files
- After Makefile changes: always `rm -rf .deps src/.deps` before rebuilding
- Never move to the next function while the current file has compile errors
- Update `annotation_status.md` after every function — do not batch updates

---

### From `dftracer-annotate-process/SKILL.md § After all passes — mandatory artifacts`

#### After all passes — mandatory artifacts

```bash
diff -urN source/ annotated/ > workspaces/<run_id>/annotation.patch
```

Also write `annotation_logs/annotation_report.md` with:
- Total files modified, lines added, macros inserted
- Per-pass build history
- Table of annotated functions (file, function, macros, notes)
- Table of skipped functions (file, function, reason — Rule 0 only)
- Table of INCLUDE-ONLY functions with retry plan

---

### From `dftracer-annotate-project/SKILL.md § Steps`

#### Steps

1. Load the sibling files in this skill directory.
2. Scope the target files with MCP discovery tools.
3. Dispatch C, C++, and Python files to the matching file-type subagent.
4. Validate the annotated set and report coverage.

### From `dftracer-annotate-project/SKILL.md § Included Context`

#### Included Context

- Load `rules.md`
- Load `permissions.md`
- Load `pitfalls.md`
- Load `lessons.md`

### From `dftracer-annotate-project/SKILL.md § Last Step`

#### Last Step

Before stopping, update the sibling lesson files with any new annotation routing
issue, file-type split issue, or tool failure discovered during the session.

### From `dftracer-annotate-project/rules.md § Rules`

#### Rules

- Keep project annotation separate from file rewriting.
- Use the file-type subagents for the actual macro insertion.
- Prefer scoped batches over whole-repo churn when a smoke test can filter files.

### From `dftracer-annotation-lessons/SKILL.md § Related Skills`

#### Related Skills

Workload-specific lessons and pitfalls live in dedicated skills — load these when working on the corresponding application:

- **[[workload-ior]]** — IOR build quirks, annotation pitfalls, ROMIO/VAST tuning, smoke test
- **[[workload-h5bench]]** — H5Bench build, CMake quirks, assert/else-if brace insertion, INI config

Software-specific optimization strategies:

- **[[software-mpi]]** — MPI-IO, ROMIO hints, Flux env propagation, Cray MPICH
- **[[software-hdf5]]** — HDF5 version, chunk/cache tuning, Cray chid_t, dftracer HDF5 support
- **[[software-posix]]** — POSIX readahead, lustre striping, OS tuning, ops_slope bottlenecks

When appending new session lessons below, also update the workload or software skill file that matches the `app:` field and `tags:` — see Step 9 of [[dftracer-pipeline]].

---

### From `dftracer-annotation-lessons/SKILL.md § Standing rules (always apply, every session)`

#### Standing rules (always apply, every session)

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

### From `dftracer-annotation-lessons/SKILL.md § Core Annotation Rules`

#### Core Annotation Rules

##### ALWAYS_ANNOTATE (never apply Rule 0 skip to these)

These function categories must always be annotated, regardless of body length or
perceived complexity:

  - Lifecycle:   *_init, *_final, *_initialize, *_finalize
  - Sync/flush:  *_fsync, *_flush, *_sync
  - File ops:    *_delete, *_rename, *_stat, *_mknod, *_getfilesize
  - Vendor FS:   gpfs_*, beegfs_*, lustre_*, hdfs_*, ceph_*, daos_*

##### Rule 0 — Skip criterion

Apply Rule 0 (skip without annotation) ONLY when ALL of the following are true:
  1. The function is a pure getter or setter with no more than 5 lines
  2. It returns a single field
  3. It performs no I/O, no data movement, and no syscalls

Every Rule 0 skip must be justified by function name in the per-file report.

##### comp= Classification Table

  "io"   — file I/O (POSIX open/read/write/close/stat/mmap), HDF5, NCMPI,
            backend lifecycle (init/final/initialize/finalize), vendor FS helpers
  "comm" — MPI wrappers, network I/O, distributed FS clients (S3, HDFS, RADOS, DFS)
  "mem"  — memcpy, large buffer alloc/free, mmap region setup, tensor copies
  "cpu"  — checksums, compression, encryption, hashing

C note:   transfer to/from a file (POSIX/MMAP/HDF5/NCMPI) → "io";
          transfer via network/MPI → "comm"; memcpy into mmap → "mem"
C++ note: MPI calls wrapped in C++ → "comm"; std::filesystem / fstream → "io"
Python:   comp= classification uses the same table

##### Per-Function Incremental Annotation Loop (Step 1.5 rule)

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

##### DFTRACER_C_INIT third argument is `int*`, never a literal int

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

### From `dftracer-cheatsheet/SKILL.md § Annotation Quick Reference — Critical Rules, Corner Cases, Known Mistakes`

#### Annotation Quick Reference — Critical Rules, Corner Cases, Known Mistakes

Read this first. Every item here is drawn from a real session failure or a
rule that is commonly missed. Details and code examples are in the topic files.

---

### From `dftracer-cheatsheet/SKILL.md § CRITICAL — Violations cause build failures or silent empty traces`

#### CRITICAL — Violations cause build failures or silent empty traces

| # | Rule | What breaks | Detail |
|---|------|-------------|--------|
| C1 | `DFTRACER_C_FUNCTION_START()` must be the **first statement after `{`**, never before it | Syntax error | C Rule 1 |
| C2 | `DFTRACER_C_FUNCTION_END()` must be **before every `return`**, never after it | Unreachable code / missing span | C Rule 2 |
| C3 | `DFTRACER_C_FUNCTION_UPDATE_STR("comp", "<type>")` is **mandatory immediately after START** — never skip it | Trace analysis can't group functions | C Rule 4, Pitfall: comp= |
| C4 | **Never annotate a forward declaration** — only the definition with a `{...}` body | Syntax error | C Rule 7, Pitfall: POSIX_Xfer |
| C5 | If code has `DFTRACER_C_INIT()`, **do NOT set `DFTRACER_INIT=1`** in the environment | Empty trace file | Standing Order 6, Pitfall: INIT conflict |

---

### From `dftracer-cheatsheet/SKILL.md § Coverage Verification Commands (run after every file)`

#### Coverage Verification Commands (run after every file)

```bash
FILE=annotated/src/aiori-POSIX.c

# 1. START count must equal comp= count
grep -c "DFTRACER_C_FUNCTION_START"               $FILE
grep -c 'DFTRACER_C_FUNCTION_UPDATE_STR.*"comp"'  $FILE

# 2. List all annotated function signatures
awk '/^[a-zA-Z_].*\(/ {func=NR": "$0} /DFTRACER_C_FUNCTION_START/ {print func}' $FILE

# 3. List all definitions (compare against #2 to find gaps)
grep -n "^[a-zA-Z_].*(.*)$" $FILE | grep -v ";"
```

Both counts from step 1 must match. Any definition in step 3 not in step 2 is a
candidate for annotation (check the body for syscalls before deciding to skip).

## Constraints

- Annotate only the session's annotated/ tree; source/ stays pristine (it is the reset point).
- Never edit source by hand when an MCP annotation tool can do it; hand edits are the least trusted path and always re-validated.
- Never hand off to the build step until validate_annotations and dftracer_annotate_check pass.

### From `dftracer-annotate-process/SKILL.md § Context economy: query the graph, don't read the tree`

#### Context economy: query the graph, don't read the tree

Before any step that would open source files, use the `graphify` knowledge graph
(project dependency `graphifyy`, CLI `graphify`):

```bash
graphify query "<target>" --budget 1200   # locate: NODE <sym> [src=file loc=Lnn]
graphify explain <symbol>                 # definition + callers/callees
graphify affected <symbol> --depth 2      # blast radius before you change it
graphify update .                         # refresh after edits (~4s, no LLM)
```

Measured on this repo: locating cost 986 tokens vs 29,456 to read the three
relevant files (3.3%). Run `affected` before editing any shared function and
state the blast radius. Use the CLI, never `graphify-mcp` — its extra tool
schemas would sit in context permanently. See [[dftracer-context-economy]].

### From `dftracer-annotate-project/permissions.md § Permissions`

#### Permissions

- Operate inside the current session workspace only.
- Read source and annotated trees for the active run.
- Do not write source outside the session-owned paths.

### From `dftracer-cheatsheet/SKILL.md § S0 — STRICT RULE: always use the session's own directory structure`

#### S0 — STRICT RULE: always use the session's own directory structure

**Never invent your own paths for source, build, traces, patches, or
artifacts.** The session workspace (`workspaces/<app>/<run_id>/`) has one
canonical layout — `baseline/`, `annotated/`, `opt<n>/` (each with
`source/`, `scripts/`, `traces/{raw,compact}`, `patches/`), plus
`artifacts/`, `tmp/`, `dataset/`, `session_report.md`, `session.json`. This
applies at **every** stage — annotation, build, smoke test, trace
collection, optimization iterations — not just file cloning.

- Get paths from the MCP tools (`session_get_run_paths`, `session_create`,
  `session_init_structure`) or from `session.json["paths"]` — never
  construct a path like `ws/build_ann` or `ws/traces` by hand.
- **Before annotation and before/after every optimization iteration**, call
  `session_validate_structure(run_id)`. If it reports `clean: false`, call
  `session_reorganize_structure(run_id, dry_run=False)` before continuing.
  Do not proceed on a drifted workspace.
- `session_reorganize_structure` never deletes data — legacy/drifted paths
  are quarantined under `artifacts/legacy/<name>/`, never guessed into a run
  directory.

---

### From `dftracer-cheatsheet/SKILL.md § Environment consistency (MANDATORY, applies to every step)`

#### Environment consistency (MANDATORY, applies to every step)

The application defines the environment, not the site defaults. Before touching modules,
compilers, or a venv, read the app's own scripts and reuse them VERBATIM:
`<app>/scripts/install-<system>.sh`, `<app>/scripts/<app>-<system>.job`, `pyproject.toml`.

- **install env == run env.** Same python, modules, `LD_PRELOAD`, `LD_LIBRARY_PATH`, patchelf steps.
- **Install dftracer in the SAME script and venv as the app** (critical for DL workloads,
  whose torch/mpi4py wheels pin an exact MPI/ROCm/Python ABI).
- **Bind `CC`/`CXX` to the MPI the app uses.** `which mpicc` may be the wrong wrapper; linking
  dftracer against a different MPI than the app preloads aborts at exit (`double free`).
- Pass MPI (and HDF5 only if the app uses it) explicitly to dftracer via ENV VARS.
- A zero exit code does not mean tracing worked. Verify `python -c "import dftracer.dftracer"`
  and that a NON-EMPTY `.pfw` was produced.

See the `dftracer-install` skill, RULE 0-5.

## Corner cases

### From `dftracer-annotate-project/pitfalls.md § Pitfalls`

#### Pitfalls

- Mixing discovery and mutation in one step makes annotation hard to debug.
- A single generic annotator loses file-type context and wastes tokens.
- Skipping the coverage report hides missing files.

### From `dftracer-annotation-lessons/SKILL.md § General Pitfalls (PG)`

#### General Pitfalls (PG)

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

### From `dftracer-annotation-lessons/SKILL.md § Fortran Entry Point Pitfall (PF)`

#### Fortran Entry Point Pitfall (PF)

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

### From `dftracer-cheatsheet/SKILL.md § CORNER CASES — Non-obvious patterns that need special handling`

#### CORNER CASES — Non-obvious patterns that need special handling

##### CC1: Two occurrences of the same function name
A C file may have a forward declaration near the top and the real definition further
down. Both have the same name but only the definition has a `{...}` body.

```bash
grep -n "POSIX_Xfer" aiori-POSIX.c
# 115: static IOR_offset_t POSIX_Xfer(int, aiori_fd_t *, ...);   ← SKIP (fwd decl, ends with ;)
# 735: static IOR_offset_t POSIX_Xfer(int access, aiori_fd_t *file, ...) ← ANNOTATE (definition)
```
Quick filter — shows only definitions: `grep -n "FN_NAME" file.c | grep -v ";$"`

##### CC2: Error-checking macros hide early returns (Rule E)
`MPI_CHECK(...)`, `NCMPI_CHECK(...)`, `HGOTO_ERROR(...)` expand to `if (rc != 0) { ... return; }`.
These are invisible exit points. Do NOT place `END` before them — only place `END` before
**visible** `return` and `throw` statements in the source.

For **goto-based cleanup** (HDF5): place a single `END` at the `done:` / `err:` label,
not before each individual `goto`. See C Rule 5 (error macros) and 01-annotation-rules-general.md Rule E.

##### CC3: Multi-line function signatures
When a function signature spans multiple lines the `{` appears alone on a line below the last
parameter. Scan **upward** from the `{` to find the signature start before placing START:
```c
static IOR_offset_t POSIX_Xfer(int access, aiori_fd_t *file,
                               IOR_size_t *buffer, IOR_offset_t length,
                               IOR_offset_t offset, aiori_mod_opt_t *param)
{                         ← START goes here, after this brace
  DFTRACER_C_FUNCTION_START();
```

##### CC4: Vendor filesystem functions guarded by `#ifdef`
`gpfs_*`, `beegfs_*`, `lustre_*` functions are compiled only when `HAVE_GPFS_FCNTL_H`
etc. are defined. Annotate them anyway — they will appear in traces when the binary
is built against those libraries and run on the corresponding filesystem.
If they don't appear in a basic smoke test, that is **expected, not a bug**.

##### CC5: Internal helper functions with non-obvious names
After annotating the public API functions (`POSIX_Create`, `POSIX_Xfer`, etc.) always
scan the **entire file** for unannotated definitions. Any helper that calls a syscall
or filesystem API is an I/O function regardless of its name:

```bash
# List all annotated functions
awk '/^[a-zA-Z_].*\(/ {func=NR": "$0} /DFTRACER_C_FUNCTION_START/ {print func}' file.c

# Find definitions that may be missing
grep -n "^[a-zA-Z_].*(.*)$" file.c | grep -v ";"
```

**Triggers that make a helper mandatory to annotate:**
- Calls `open()`, `read()`, `write()`, `close()`, `mknod()`, `mkstemp()`, `stat()`, `ioctl()`
- Calls `cuFileHandleRegister()`, `cuFileRead/Write()` (GPU Direct)
- Calls `gpfs_fcntl()`, `beegfs_*()`, `llapi_*()` (vendor filesystem APIs)
- Creates, opens, reads, writes, or deletes any file
- Returns a file descriptor

**Apply Rule 0 (skip) only if:** the function is a pure getter/setter ≤5 lines, a
string formatter, or an arithmetic helper with no filesystem calls.

Examples from `aiori-POSIX.c`:
| Function | Decision | Reason |
|----------|----------|--------|
| `init_cufile` | ✅ Annotate `comp="io"` | Calls `cuFileHandleRegister()` — GPU Direct I/O setup |
| `mkTempInDir` | ✅ Annotate `comp="io"` | Calls `mkstemp()` + `unlink()` — creates and deletes a file |
| `POSIX_Mknod` | ✅ Annotate `comp="io"` | Calls `mknod()` syscall |
| `POSIX_xfer_hints` | ❌ Skip (Rule 0) | 2-line setter, no filesystem call |
| `POSIX_check_params` | ❌ Skip (Rule 0) | Pure parameter validation, no filesystem call |
| `cuFileGetErrorString` | ❌ Skip (Rule 0) | Trivial error string formatter |

##### CC6: Lifecycle functions that look too short to annotate
Backend `*_Initialize` and `*_Finalize` functions often have short bodies (sometimes
just an `#ifdef` block). **Always annotate them** — they mark the backend entry/exit
point in traces and are never skipped under Rule 0.

##### CC7: `beegfs_isOptionSet`-style END-after-return bug
When a function has a single-expression return like `return opt != -1;`, it is easy
to place `END` after the `return` by accident, making it unreachable:
```c
// ❌ Wrong — END after return is dead code
bool beegfs_isOptionSet(int opt) {
  DFTRACER_C_FUNCTION_START();
  return opt != -1;
  DFTRACER_C_FUNCTION_END();   // ← unreachable, span never closed
}

// ✅ Correct — END before return
bool beegfs_isOptionSet(int opt) {
  DFTRACER_C_FUNCTION_START();
  DFTRACER_C_FUNCTION_UPDATE_STR("comp", "io");
  DFTRACER_C_FUNCTION_END();   // ← before the return
  return opt != -1;
}
```

---

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector

dftracer_annotate_check reports one row per failed check; repair the input or the tool, then annotate and check again:
- `<file>: compiles` / `parses` false: fix the reported line with the language capability's tools (dftracer_annotate_clang: clang_syntax_check, clang_lint_annotations, clang_add_braces; dftracer_annotate_python: python_lint_annotations, python_dedup_annotations). If the error is a missing system header, pass `extra_include_dirs` / `extra_flags` (-DHAVE_CONFIG_H for autotools) rather than changing code.
- `comp UPDATE first` false: insert the comp UPDATE right after each START (C3, M1); re-run clang_annotate_file rather than hand-editing.
- `END before every exit` false: a braceless `if (x) return` needs clang_add_braces first; otherwise insert END before the return (C2).
- `main: INIT and FINI` / `project: init|fini present` false: insert INIT first in main and FINI before every exit, before MPI_Finalize or its wrapper (C Rule 3); for Python `_dft_log = dftracer.initialize_log(...)` and `_dft_log.finalize()`.
- `critical I/O calls annotated` / `coverage and cost gate` false: annotate the named functions (their calls are listed); for an over-annotated Python function remove the decorator or use a dft_ai region.
- `project: app-parameter metadata` false: annotate_add_app_metadata(run_id, <entry file>, language, params_json) after the init (or with anchor_regex after argument parsing).
- `no macros in headers` false: move the macros to the .c/.cpp definition (R5); template headers are allowed.
The same failure twice stops the loop: report the exact `file:line` findings to the user instead of claiming the tree is annotated.

### From `dftracer-annotate-process/SKILL.md § Mandatory final validation gate (ALWAYS — even after manual fixes)`

#### Mandatory final validation gate (ALWAYS — even after manual fixes)

Annotation is not finished when files are written; it is finished when validation
passes. Run this LAST on every path — MCP fast path, prose backup path, or a
hand-edit after a tool failed:

```
validate_annotations(run_id=RUN_ID, language="python")   # or "c" / "cpp"
```

then dispatch the matching validator agent (`dftracer-validate-python` /
`-c` / `-cpp`).

`ml_annotate_project` already runs this internally (`validate=True`) and returns
`validation.passed`. **That does not excuse the manual path.** The dangerous
sequence is: MCP tool errors → agent hand-edits the file → nobody re-checks. A
hand edit is the least-trusted change in the pipeline, and a tool that errored
may have left a file half-written.

Do not proceed to the build, and do not report success, until validation returns
`passed: true` with zero findings and zero project issues. Otherwise report the
findings verbatim (`file:line`, function, the uninstrumented critical call) and
escalate.

It enforces: every I/O / checkpoint / collective-comm function instrumented;
init AND finalize present (a missing finalize truncates the trace); app-parameter
metadata emitted; annotated functions pass the cost gate — with `dft_ai.*`
AI-API regions exempt; and every file still parses.


---

### From `dftracer-cheatsheet/SKILL.md § KNOWN MISTAKES — Top recurring errors from real annotation sessions`

#### KNOWN MISTAKES — Top recurring errors from real annotation sessions

| # | Mistake | Symptom | Fix |
|---|---------|---------|-----|
| M1 | **comp= not added** after completing a file | `grep -c START` ≠ `grep -c comp=` | Add `UPDATE_STR("comp","<type>")` immediately after every bare START |
| M2 | **Forward declaration annotated** instead of definition | `syntax error: expected ';'` before `{` | Check both occurrences; annotate only the one with a body |
| M3 | **Vendor functions skipped** (gpfs_*, beegfs_*, lustre_*) | Coverage gap found in post-annotation awk scan | These are I/O functions — always annotate |
| M4 | **Lifecycle functions skipped** (Initialize, Finalize) | Functions missing from trace | Never apply Rule 0 to lifecycle functions |
| M5 | **Internal helpers missed** (init_cufile, mkTempInDir, etc.) | Coverage gap | Run the awk scan after the named functions; check every definition against the syscall trigger list |
| M6 | **`.deps missing separator`** after Makefile edit | Build fails immediately | `rm -rf .deps src/.deps && make clean` — always after any Makefile change |
| M7 | **END indented at column 0** | Compiles, but hard to read / wrong indentation | END must match the indentation of the surrounding `return` |
| M8 | **`DFTRACER_INIT=1` with explicit INIT call** | Empty `.pfw` trace file | Pass `env_extra='{"DFTRACER_INIT":"0"}'` when source has explicit `DFTRACER_C_INIT()` |
| M9 | **Missing `rm -rf .deps`** after annotating a file that affects the Makefile | `.deps missing separator` on next build | Standard cleanup before every `make` after Makefile touch |
| M10 | **Completing annotation without verifying coverage** | Missed functions discovered late | Always run the two-count check and awk scan at end of every file |

---

## Self-heal

- If validate_annotations or dftracer_annotate_check is wrong (a false positive confirmed by reading the file), fix the tool here with capability_update and record the case as a lesson; do not work around it by hand-editing source.

## Self-learn

- What is explored here becomes a capability: see capability_builder. A new annotation failure is a lesson here (LESSONS.md, symptom -> root cause -> fix, dated); a new verified recipe is a WORKED.md entry; a new workload or software met while annotating becomes workload_<x> / software_<x>.

### From `dftracer-annotate-process/SKILL.md § Pass 5 — Review, Confirm, and Extract Rules (MANDATORY — do not skip)`

#### Pass 5 — Review, Confirm, and Extract Rules (MANDATORY — do not skip)

##### Step 1 — Present the coverage summary to the user

Show the user these three tables from `annotation_status.md` inline:
1. **Coverage Summary table**
2. **Annotated functions table** — all rows with ✅ DONE
3. **Skipped/Rule-0 table**

Ask explicitly:
> "Annotation complete. Please review:
> - Are there any functions in the Skipped list that you think should be annotated?
> - Are any functions in the Annotated list incorrect (wrong comp type, wrong macros)?
> Reply 'confirmed' to proceed, or tell me what to fix."

**Do not proceed to Step 2 until the user replies 'confirmed'.**

##### Step 2 — Extract new rules from the session log

Read `annotation_logs/annotation_process.log` and extract every pattern that meets ANY criterion:
- A function was initially skipped/missed and had to be added later
- A build failed and required a specific fix
- A comp type was wrong and had to be corrected
- A macro was placed wrong
- A new function category was discovered
- A user correction during the review step

For each extracted pattern, add a pitfall entry to `.goose/hints/02-annotation-pitfalls.md`.

##### Step 3 — Update the cheatsheet coverage verification commands

Check whether the coverage verification commands in `00-critical-cheatsheet.md`
would have caught the new pattern. If not, add a new check command.

##### Step 4 — Write the rule-update log

```
[RULES] <date> — extracted N new rules from session
  - pitfalls added: <list>
  - mandatory list updates: <functions added>
  - cheatsheet updates: <section updated>
```

**The session is not complete until Steps 1–4 are done.**


---

### From `dftracer-annotate-project/lessons.md § Lessons Learned`

#### Lessons Learned

- Route by language before annotating.
- Keep the project agent thin and use the language agents for the details.
- Record repeated annotation misses back into the relevant file-type skill.

### From `dftracer-annotation-lessons/SKILL.md § How to use this file`

#### How to use this file

Read this before annotating any file. For each lesson:
  1. Check if the `context` matches what you are about to do
  2. If so, apply the `fix` proactively — do not repeat the mistake

### From `dftracer-annotation-lessons/SKILL.md § How to add new entries`

#### How to add new entries

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

### From `dftracer-annotation-lessons/SKILL.md § Session logs (appended by pipeline Step 8)`

#### Session logs (appended by pipeline Step 8)

The accumulating dated lesson entries live in a separate sibling file,
`LESSONS_LOG.md`, so this instruction file stays compact regardless of how
many sessions have contributed to it. Load the log with:

    skill_load(name="dftracer-annotation-lessons", file="LESSONS_LOG.md")

`session_ml_append_lesson` and `session_lessons_sync_pr` both target
`LESSONS_LOG.md` directly — new entries are appended there, after the same
anchor comment, never into this file.

### From `dftracer-lessons/SKILL.md § REQUIRED RULE 0: Skills first, and capture every Q&A as a lesson (MANDATORY)`

#### REQUIRED RULE 0: Skills first, and capture every Q&A as a lesson (MANDATORY)

Before installing, building, annotating, running, tuning, or debugging anything: run
`skill_search` / `graph_query(mode="docs")` / `skill_load` and act on what the system
already knows. Never reason from model priors or a web search when a
`workload-*` / `system-*` / `software-*` / `tools-*` skill already covers the task.

Every question the user answers, and every correction the user gives, is durable
knowledge — not a session detail. Record it AS YOU GO, not just at the end:
preferences/"always-never" rules into the relevant skill AND the agent YAML (then
`agents_sync`); effort/session state via `memory_write(type=project)`; standing
guidance via `memory_write(type=feedback)` with the why; deterministic reusable
logic into an MCP tool.

A GENERIC rule (one that should apply to every agent, not just one skill's scope)
must reach ALL skills and agents: add a shared section in
`agents/common-sections.yaml`, `include:` it in every agent template that needs it,
run `agents_sync`, and reflect it in the process skills. A generic rule filed in one
corner of one skill has not been learned by the system — this is the same shared
`skills-first-and-capture-every-qa` section every agent template includes.

This governs WHEN and WHAT to capture; the "Confirmation gate" below still governs
whether a captured lesson gets PERSISTED to a shared skill/tool/agent without the
user's sign-off first.

### From `dftracer-lessons/SKILL.md § Lessons-Learned Cache`

#### Lessons-Learned Cache

A structured log of past failures and their fixes lives at `.goose/lessons-learned.md`.

**Before attempting any build, install, configure, or annotation step:**
1. Read `.goose/lessons-learned.md`.
2. Search for entries whose `tags` or `error` match the current task (package name, build tool, error text).
3. Apply the recorded fix proactively — do not repeat a known failure.

**After resolving any non-trivial error (not a simple typo):**
Append a new entry to `.goose/lessons-learned.md` using this format:

```markdown
---
date: YYYY-MM-DD
context: <one-line description of what was being attempted>
error: |
  <exact error message or key excerpt>
root_cause: <why it happened>
fix: |
  <exact steps or code change that resolved it>
tags: [<package>, <build-tool>, <error-keyword>, ...]
---
```

Keep entries cumulative — never delete old ones. They are the institutional memory for this project.

### From `dftracer-lessons/SKILL.md § Naming disambiguation: `workload-<app>` vs `software-<app>` (MANDATORY)`

#### Naming disambiguation: `workload-<app>` vs `software-<app>` (MANDATORY)

Before creating a new per-app skill, decide which family it belongs to —
this governs ~20 existing skill names and is not otherwise documented in any
skill or agent:

- **`workload-<app>`** — a scientific/HPC workload dftracer TRACES to study
  I/O behavior (the thing under study). Examples: `workload-ior`,
  `workload-h5bench`, `workload-flashx`, `workload-vpic-kokkos`,
  `workload-scaffold`, `workload-1000genome`.
- **`software-<app>`** — a tool, service, or DL application that is itself
  being ANNOTATED/INSTRUMENTED by dftracer as software under test, not a
  scientific workload in its own right. Examples: `software-flux-fiction`
  (a Flux scheduling emulator), `software-molformer`, `software-pecan`,
  `software-ray` (annotated DL training apps use `software-*`, not
  `workload-*`, even though they are the thing being traced — the
  distinction is "scientific/HPC I/O workload" vs "software application").

**Why:** an auto-created `workload-flux-fiction` skill was corrected by the
user, who clarified flux-fiction is software annotated by dftracer, not a
workload. The same distinction applies to every annotated app in this
project, not just that one instance.

**How to apply:** before creating a new per-app skill during a build/annotate
session, judge whether the target is a traced scientific workload
(`workload-*`) or a tool/service/DL-training-app being instrumented
(`software-*`), and ask the user if genuinely ambiguous rather than
defaulting to `workload-*`.

### From `dftracer-lessons/SKILL.md § Growing the skills every session (generic vs specific routing)`

#### Growing the skills every session (generic vs specific routing)

Every agent is expected to make the skills smarter each run — capture not just
failures but the working recipe (exact commands, flags, paths, versions,
caveats) so the next session reuses it instead of rediscovering it.

Route each learning to the right home:

- **Generic, cross-workload knowledge** → the relevant GENERIC skill
  (e.g. `dftracer-annotate-*`, `software-hdf5`, `software-mpi`, `software-posix`).
  Keep these skills generic: they hold the general procedure that applies
  everywhere.
- **Workload-specific** caveats → `workload-<app>` (e.g. `workload-flashx`).
- **System/site-specific** quirks → `system-<system>` (e.g. `system-tuolumne`).
- **Library-specific** details → `software-<lib>`.
- Create the specific skill if it does not exist.

Prefer: generic skill = the general how-to; specific skill = only the
workload/system/software deltas layered on top.

### From `dftracer-lessons/SKILL.md § Redaction gate (MANDATORY)`

#### Redaction gate (MANDATORY)

Lessons, skills, agent definitions and memory are git-tracked and ship to other
people. **Redact before you persist.** We learn from experience; we never record
who ran it.

Never write into any of them: usernames or real names, email addresses, absolute
user paths (`/usr/WS2/<user>/...`, `/p/lustre5/<user>/...`, `/g/g92/<user>/...`,
`/home/<user>/...`), flux job ids, session UUIDs, or node hostnames. Write
`$USER`, `$PROJECT_ROOT`, `$LUSTRE_ROOT`, `$HOME`, `<flux-jobid>`, `<uuid>`,
`<system><node>` instead. Keep the lesson; drop the provenance.

Verify with the deterministic tools rather than by reading:

```
privacy_scan()                 # report identifying content, read-only
privacy_redact(dry_run=True)   # show what would change
privacy_redact()               # rewrite in place
```

Citation lines (`**Citation:**`, `**Authors:**`) are public bibliography and are
exempt. The `dftracer-privacy-guard` agent runs this as the last step of every
session, but that is a backstop — do not rely on it to clean up after you. Load
[[dftracer-privacy-guard]] for the full table and the known false positives.

### From `dftracer-lessons/SKILL.md § Confirmation gate (MANDATORY)`

#### Confirmation gate (MANDATORY)

Agents must NOT self-write skills, lesson files, agent definitions, or MCP
tools. Instead, PROPOSE each update back to the main thread (target skill/tool
+ what you did / symptom → root cause → exact content). The main thread confirms
the observation with the user, and only then is it persisted. This keeps
incorrect diagnoses out of the shared institutional memory while still capturing
learning aggressively.

## What works

Verified recipes, dated, as in WORKED.md (full list there):

- [ygm/pipeline/session-build-annotated-s-build-subdir-parameter] 2026-08-04: Fixed in session_tools.py: introduced a single `src_root = ann / build_subdir if build_subdir else ann` resolved once near the top of _session_build_annotated_impl (with an existence check), and replaced every bare `ann` reference in the cmake/autotools/meson/python branches with `src_root`. The custom_build_cmd escape hatch now also just uses src_root instead of re-deriving `work` locally. Requires an MCP server restart to take effect (code change, not data) — until restarted, use the custom_build_cmd escape hatch (which already correctly honored build_subdir even before this fix) as an immediate workaround for any project with a nested annotated/<dir>/ layout: pass a manual `cmake -S . -B ../../build_ann ... && cmake --build ../../build_ann && cmake --install ../../build_ann` as custom_build_cmd with build_subdir set — custom_build_cmd's cwd is src_root, so relative paths climb back to the workspace root correctly. (resolved: session_build_annotated(run_id, build_subdir="source", extra_cmake_flags=...) failed cmake configure with: "The source directory <ws>/annotated does not appear )
- [ygm-bench/annotate/making-a-second-separate-repo-build-against-an-a] 2026-08-04: Pass CMake's own per-dependency override cache variable at configure time: cmake ... -DFETCHCONTENT_SOURCE_DIR_YGM=<ws>/annotated/source ... (the <NAME> must match the FetchContent_Declare(<name> ...) name, uppercased — "ygm" -> FETCHCONTENT_SOURCE_DIR_YGM). This makes FetchContent_MakeAvailable add_subdirectory() the local annotated tree in place instead of git-cloning, with NO CMakeLists.txt patch needed in either repo. Because the annotated library's own CMakeLists.txt already links its dftracer_core_imported target into its main INTERFACE target, any downstream consumer that links that target (e.g. ygm-bench's own `target_link_libraries(... ygm::ygm)`) transitively picks up the dftracer link automatically — no separate dftracer wiring needed in the downstream repo's CMakeLists.txt. Caveat: the downstream repo's own entry points (main()s) still need their own explicit DFTRACER_CPP_INIT()/DFTRACER_CPP_FINI() added (mirroring the first repo's smoke-test entry point) — without it, every DFTRACER_CPP_FUNCTION() call reached via the annotated library is a silent no-op (dftracer is never initialized for that process). General pattern: this FETCHCONTENT_SOURCE_DIR_<NAME> override applies to ANY downstream/benchmark repo that pulls the traced library via FetchContent — not YGM-specific. (resolved: (not an error — a generic, reusable CMake technique worth recording) ygm-bench's CMakeLists.txt does find_package(ygm CONFIG) first, falling back to FetchConten)
- [general/pipeline/dftracer-prerelease-wheel-dftracer-dist-module-p] 2026-08-04: When dftracer_service is required (project policy rule 12, every job launch) and only the prerelease wheel is installed, install a SECOND, from-source dftracer into a separate session-local venv (e.g. venv-src-gnu) using the GNU toolchain, and use ITS dftracer_service binary — while still building/linking the actual traced application against whichever dftracer install matches its own compiler ABI (GNU toolchain build in this session, since Cray-clang 20 couldn't compile ygm-bench's spdlog dependency anyway; see [[system-tuolumne]]). Do not assume the prerelease wheel is a complete substitute for a from-source install if dftracer_service is needed. (resolved: <venv-311>/bin/dftracer_service not found when trying to bracket a run per project policy rule 12 (node-counter daemon, one instance per node).)
- [rajaperf, general/pipeline/reconfirmation-of-the-mcp-dftracer-analyze-dask] 2026-08-05: No new fix applied. Flagging that trace SIZE alone does not predict whether this hang reproduces -- don't assume a small trace is safe from it. If this recurs, the next investigation should check whether analyze() is hanging during the ANALYSIS phase itself (not just dask teardown) for this trace shape/preset combination, since zero stdout was captured this time vs. the original bug report's "output already flushed, only teardown hangs" — that's a materially different symptom and may need a different fix in dfanalyzer_service.py than the existing timeout-after-output handling. (resolved: analyze() timed out at the existing 300s ceiling TWICE (generic preset, with and without a checkpoint) on an 8.6MB / ~47,588-event compact trace -- previous ent)
- [ior/pipeline/first-dftracer-pipeline-run-on-corona-intel-clas] 2026-09-10: Both fixed at the tool level (src/dftracer_agents/mcp_tools/tools/session/): (1) workspace.py: added `_autoreconf_bootstrap()` shared helper, used by both session_configure and session_build_annotated's autotools branches. On an "Autoconf version X or higher is required" failure, reads the system's actual `autoconf --version` and retries once after downgrading configure.ac's AC_PREREQ() to match, instead of failing outright. (2) install.py: the non-OpenMPI dftracer clone path now detects an auth-shaped git failure (could not read Username / Authentication failed / Repository not found / terminal prompts disabled / Permission denied (publickey) against a github.com HTTPS URL, and retries once over the equivalent `git@github.com:OWNER/REPO.git` SSH form (GIT_SSH_COMMAND with BatchMode) before giving up. Both fixes verified to parse cleanly; NEITHER took effect in the session that found them (dftracer MCP server runs over stdio, one process per session, no hot-reload — contrary to the dftracer-mcp-first skill's HTTP/--reload claim, which describes a different deployment mode). They apply starting the NEXT session against this codebase. (resolved: session_configure (autotools): "[Errno 2] No such file or directory: '.../source/configure'" Underlying autoreconf error: "configure.ac:4: error: Autoconf versi)
- [tool:validate_annotations] the last step of every annotation path (tools, recipe or hand edit): validate_annotations(run_id, language) must return passed: true with zero findings and zero project issues before the build; then dftracer_annotate_check on the tree. (dftracer-annotate-c agent, mandatory final validation gate)
