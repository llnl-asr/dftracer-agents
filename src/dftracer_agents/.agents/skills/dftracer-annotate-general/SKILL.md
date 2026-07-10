---
name: dftracer-annotate-general
description: Language-agnostic dftracer annotation rules — Rule 0 (what to skip), coverage, error paths, backend priority order, and wrapper handling
---

## C / C++ / Python Annotation Rules (dftracer)

These rules apply whenever you manually annotate C, C++, or Python source files.
Violating any rule will cause build failures or missing trace data.

### Rule 0 — Only annotate functions worth tracing (skip trivial ones)

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

**Always annotate, even if they look small:**
- Backend lifecycle functions (`*_init`, `*_final`, `*_initialize`, `*_finalize`,
  `*_open_backend`, `*_close_backend`) — **Never apply Rule 0 to lifecycle functions.**
- Sync/flush functions (`*_Fsync`, `*_Sync`, `*_Flush`)
- Delete/rename/stat functions (`*_Delete`, `*_Rename`, `*_Mknod`, `*_GetFileSize`)
- **Vendor-specific filesystem helpers** (`gpfs_*`, `beegfs_*`, `lustre_*`, `hdfs_*`,
  `ceph_*`, `daos_*`) — annotate as `comp="io"`

### Rule F — Scan the entire file for internal helpers

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

### Rule D — Complete coverage: annotate everything, skip only trivial functions

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

### Rule G — Verify the write, not just the response; grep is defense-in-depth

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

### Rule E — Error paths: END only before visible return/throw

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

### Rule A — Annotate at every abstraction layer (wrapper calls)

When a higher-level function delegates to a lower-level annotated function, annotate BOTH:
```
MMAP_Xfer()      ← annotate (captures MMAP-level intent)
  └─ POSIX_Xfer() ← also annotated (captures syscall-level detail)
```

### Rule B — Annotation order within a file: simplest functions first

1. Functions with a **single return statement**
2. Functions with **2–3 returns** in shallow control flow
3. Functions with **multiple returns in nested blocks**
4. Functions with **error-checking macros** that may hide exits

### Rule C — Backend annotation priority order

1. **Reference / POSIX backend** — simplest, establishes the pattern
2. **Wrapper backends** (MMAP, AIO) — wrap POSIX
3. **MPI-based backends** (MPIIO, NCMPI, parallel HDF5)
4. **High-level library backends** (HDF5, NetCDF)
5. **Distributed / cloud backends** (HDFS, DFS, S3, CEPHFS, RADOS)
6. **Specialized / research backends** (PMDK, IME, Gfarm, CHFS, aio)

Build and run a smoke test for each backend immediately after annotating it.

### Fortran Programs — No C main() available

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

### Quick checklist before writing annotated code

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
