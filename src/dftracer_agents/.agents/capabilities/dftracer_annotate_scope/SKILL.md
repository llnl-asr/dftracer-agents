---
name: dftracer_annotate_scope
description: Decide what to annotate before annotating: session_identify_smoke_test_files (only the files a smoke command reaches) and the Python cost gate (python_estimate_function_cost, python_estimate_file_costs), with Rule 0 skip, ALWAYS_ANNOTATE lifecycle/vendor functions and hot-loop exclusion. Load for large codebases, trace noise from over-annotation, 'annotated but fails the cost gate', hot loops dominating a trace, or choosing exclude_functions.
---

# dftracer_annotate_scope

Converted 2026-11-13 from dftracer-agents: converted from dftracer-agents register_annotation_filter_tools (session/annotation_filter.py) and register_python_cost_tools (session/python_cost.py), with the Rule 0 / ALWAYS_ANNOTATE rules of dftracer-annotation-lessons (copy). Tools keep their dftracer-agents names, signatures and behavior; the validator dftracer_annotate_scope_check rules on what the predictors produce.

## Rules

- Predict with session_identify_smoke_test_files, python_estimate_function_cost, python_estimate_file_costs; validate with dftracer_annotate_scope_check; on a failed check follow ## Corrector, then predict and validate again.

### From `dftracer-annotation-lessons/SKILL.md § Core Annotation Rules (copy)`

#### ALWAYS_ANNOTATE (never apply Rule 0 skip to these)

These function categories must always be annotated, regardless of body length or
perceived complexity:

  - Lifecycle:   *_init, *_final, *_initialize, *_finalize
  - Sync/flush:  *_fsync, *_flush, *_sync
  - File ops:    *_delete, *_rename, *_stat, *_mknod, *_getfilesize
  - Vendor FS:   gpfs_*, beegfs_*, lustre_*, hdfs_*, ceph_*, daos_*

### From `dftracer-annotation-lessons/SKILL.md § Core Annotation Rules (copy)`

#### Rule 0 — Skip criterion

Apply Rule 0 (skip without annotation) ONLY when ALL of the following are true:
  1. The function is a pure getter or setter with no more than 5 lines
  2. It returns a single field
  3. It performs no I/O, no data movement, and no syscalls

Every Rule 0 skip must be justified by function name in the per-file report.

### From `dftracer-annotation-lessons/SKILL.md § Core Annotation Rules (copy)`

#### comp= Classification Table

  "io"   — file I/O (POSIX open/read/write/close/stat/mmap), HDF5, NCMPI,
            backend lifecycle (init/final/initialize/finalize), vendor FS helpers
  "comm" — MPI wrappers, network I/O, distributed FS clients (S3, HDFS, RADOS, DFS)
  "mem"  — memcpy, large buffer alloc/free, mmap region setup, tensor copies
  "cpu"  — checksums, compression, encryption, hashing

C note:   transfer to/from a file (POSIX/MMAP/HDF5/NCMPI) → "io";
          transfer via network/MPI → "comm"; memcpy into mmap → "mem"
C++ note: MPI calls wrapped in C++ → "comm"; std::filesystem / fstream → "io"
Python:   comp= classification uses the same table

### From `dftracer-annotate-general/SKILL.md § Rule 0 (copy)`

#### Rule 0 — Only annotate functions worth tracing (skip trivial ones)

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

## Constraints

- Scoping never edits files; it only decides the file and function lists.
- Lifecycle (R6) and vendor filesystem (R7) functions are never excluded, whatever their cost.

## Corner cases

- See LESSONS.md for the dated cases met so far.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector

dftracer_annotate_scope_check failures and their repairs:
- `no header files in scope` false: drop headers from the file list; annotate the .c/.cpp definitions (template headers go through the anchor script of dftracer_annotate_cpp).
- `scope is not empty` false: re-run session_identify_smoke_test_files with the real smoke command (or scope the whole tree).
- `no lifecycle or vendor-fs function skipped` false: take them out of skip_functions / exclude_functions (R6, R7).
- `no chosen function fails the cost gate` / `every function the cost gate keeps is chosen` false: take the list from python_estimate_file_costs at the same threshold.
Check again; the same failure twice stops for the user.

## Self-heal

- If the cost model keeps or drops a function wrongly (python_cost.recommend), fix the rule in the tool and add the function to checks.json.

## Self-learn

- What is explored here becomes a capability: see capability_builder. A new annotation failure is a lesson here (LESSONS.md, symptom -> root cause -> fix, dated); a new verified recipe is a WORKED.md entry; a new workload or software met while annotating becomes workload_<x> / software_<x>.

## What works

Verified recipes, dated, as in WORKED.md (full list there):

- [general/annotate/for-large-multi-binary-c-codebases-annotate-only] 2026-07-06: Added `session_identify_smoke_test_files` MCP tool (annotation_filter.py) that: 1. Extracts binary names invoked by a smoke_cmd — preferring `strace -f -e trace=execve` against the ORIGINAL (unannotated, already-built) tree for ground truth, falling back to a static text-scan of smoke_cmd against install/bin/ contents. 2. For each binary, parses the Makefile link recipe (`$(CC) ... -o <name> <objs...>`, following backslash-continuation lines) to extract every .o object file. 3. Resolves each .o back to its .c/.cpp source, honoring the Makefile's own relative path context (e.g. `../util/foo.o` from `MontageLib/Add/Makefile` resolves to `MontageLib/util/foo.c`). 4. Returns the de-duplicated union of source files across all invoked binaries. For Montage's 10-binary mosaic pipeline (mArchiveList, mProjExec, mOverlaps, mDiffExec, mFitExec, mBgModel, mBackground/mBgExec, mAdd, mImgtbl) this narrowed 267 files to 51 — an 81% reduction. ALWAYS present the resulting file list to the user for confirmation (grouped by which binary/pipeline-stage needs it and why) before annotating, so they can adjust the binary list first if the smoke test scope was wrong. New tool registered in dftracer_service.py's session_subservice; requires MCP server restart to become callable (code changes to the server module don't hot-reload). (resolved: (not an error — a scoping/efficiency lesson) Montage has ~700 source files across 100+ independently-linked executables. Running clang_annotate_project over the)
- [montage/annotate/annotating-a-per-pixel-hot-loop-function-madd-av] 2026-07-06: 1. Added an `exclude_functions` JSON-array parameter to `clang_annotate_file` (annotation_clang.py) so specific hot-loop functions can be force-skipped regardless of the cost filter, e.g.: clang_annotate_file(run_id=..., filepath=..., exclude_functions='["avg_mean"]') (Requires an MCP server restart to take effect — see prior lessons on code changes not hot-reloading.) 2. For the live session (no restart available), manually removed the 3 DFTRACER_C_FUNCTION_START/UPDATE_STR/END lines around mAdd_avg_mean by hand — this is safe (unlike the forbidden #if-0 brace hacks) because it is a clean full-line deletion of already-syntactically-valid macro statements, not a structural patch working around a parser bug. 3. Rebuilt, re-ran the same Pegasus/PMC workflow: trace count dropped from 11.9M events (1.87GB, 3 files visible) to 62,708 events (28 files visible, 811.5MB real I/O, 740.6MB/s aggregate bandwidth) — the difference between an undiagnosable trace and a usable one. RULE OF THUMB: before trusting a trace-derived bottleneck analysis, always sanity-check `unique_file_count` and total event count against expectations for the workload size. A tiny unique-file-count with a huge event count is the signature of one hot annotated function dominating the trace — go find it (grep the largest .pfw.gz for the most frequent `name` field) before trusting the numbers. (resolved: (not a build/runtime error — a trace-quality/diagnosability failure) dfanalyzer summary on the resulting trace showed "Total Files: 3" and an EMPTY POSIX layer )
- [montage-workflow-v3/annotate/annotation-scoping-filter-must-check-the-actual] 2026-07-06: After generating (or receiving) a Pegasus DAG, always cross-check binary coverage directly against the DAG itself before declaring annotation scope complete: grep "^TASK" <run-dir>/montage-0.dag | awk '{print $2}' \ | sed 's/_ID[0-9]*_*[0-9]*$//' \ | sed 's/^\(chmod_\|stage_in_\|stage_out_\|clean_up_\|register_\|cleanup_\|create_dir_\)//' \ | sort | uniq -c | sort -rn Then for each real compute-task binary name, verify instrumentation: grep -c DFTRACER <annotated-dir>/path/to/entry_file.c Do this BEFORE running a large/expensive workflow, not after -- it's the only reliable way to confirm trace coverage matches the workflow's real execution graph, not just what was assumed during initial scoping. (resolved: Original smoke-test-scoped annotation (session_identify_smoke_test_files) covered mProject, mOverlaps, mDiffExec, mFitExec, mBgModel, mBackground/ mBgExec, mAdd)
- [flux-fiction/annotate/python-workload-with-pyproject-toml-project-scri] 2026-07-16: For Python workloads with console-script entry points: 1. Read [project.scripts] section of pyproject.toml to identify entry-point names 2. Extract module:function target (e.g., flux_fiction.cli.run_ff:main) 3. Locate the actual Python module file in source/ 4. Verify that file has dftracer.initialize_log(...) and finalize() calls at the entry function (usually inside if __name__ == "__main__" or at the top of main(). If missing, add them before running smoke tests. 5. Annotate the entry-point module with @dftracer_fn decorators on all non-trivial functions as usual (cost-gate with threshold=20 to avoid over-instrumentation of pure CLI argument parsing). When calling session_identify_smoke_test_files, pass the actual Python module path (source/src/flux_fiction/cli/run_ff.py) rather than the console-script wrapper name, or invoke the module directly via `python -m <module>` in smoke tests to bypass the console-script indirection. (resolved: session_identify_smoke_test_files looks for compiled binaries in install/bin/ and fails to locate console scripts like "flux-fiction-run" that are defined in [p)
- [general/annotate/refining-cp10-s-selective-aggregation-guidance-d] 2026-08-04: 1. Detection: after the smoke test, check observed per-call duration for annotated functions, not just static call-graph shape. Any function whose smoke-trace `dur` is very small (rule of thumb: dur < 1000 in the configured DFTRACER_TIME_METRIC unit) is a selective-aggregation candidate for the CP10 fix even if it wasn't caught by the while/spin-wait caller-pattern heuristic. 2. DFTRACER_TRACE_INTERVAL_MS sizing: scale bucket granularity to expected job duration instead of using one fixed constant everywhere. Jobs under ~5 minutes -> ~1000ms (1s) interval, so aggregated buckets still resolve phase changes; longer jobs -> grow the interval roughly proportionally (tens of minutes -> several seconds, hour+ -> tens of seconds) so the aggregated event count stays bounded without collapsing a short run's timeline into 1-2 buckets. Applied directly to CP10 in SKILL.md (see that entry for the full DFTRACER_ENABLE_AGGREGATION / rules.yaml mechanics this refines). (resolved: (not an error — a heuristic refinement to an existing lesson) CP10's SELECTIVE AGGREGATION fix only listed a fixed example DFTRACER_TRACE_INTERVAL_MS=5000 with )
- [tool:session_identify_smoke_test_files] scope a large codebase to the files a smoke command reaches before annotating, then cost-gate the remaining functions (python_estimate_file_costs); lifecycle and vendor-fs functions are always kept. (dftracer-annotation-lessons R6/R7, annotation-scoping lessons)
