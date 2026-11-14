---
name: dftracer_lessons
description: How dftracer-agents learns. Skills first and capture every Q&A as a lesson; the lessons-learned cache (.goose/lessons-learned.md) read before and appended after every build/install/annotate fix; workload- vs software- naming; generic vs specific routing of what is learnt; the redaction and confirmation gates; syncing staged annotation lessons back to the repo by PR; and the full catalogue of annotation pitfalls (real mistakes that broke builds or lost trace data, with exact fixes). Load before any build, install, configure or annotation step, when a fix should be recorded, or on an annotation build error such as 'data_fn undeclared', 'missing terminating " character', 'conflicting types for finalize_region', 'dftracer/dftracer.h: No such file', 'undefined reference to dftracer_init', 'libdftracer_core.so: cannot open shared object file', '.deps missing separator', an empty trace with DFTRACER_INIT=1, or START without END in traces.
---
Cross-references: [[dftracer_privacy]], [[dftracer_skills]], [[dftracer_report]], [[lessons]], [[knowledge]], [[capability_builder]]

# dftracer lessons and pitfalls

Related store capabilities: `lessons` (lesson_add / lesson_search / consolidation, the shared lessons store) and
`knowledge` (knowledge_search, memory_write). The annotation lessons log (`dftracer-annotation-lessons`) is converted
separately; `session_lessons_sync_*` here syncs its staged copy back to the repo.

## Rules

The two source skills follow verbatim (headings demoted one level); nothing that was learnt is dropped.

### From dftracer-lessons

### REQUIRED RULE 0: Skills first, and capture every Q&A as a lesson (MANDATORY)

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

### Lessons-Learned Cache

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

### Naming disambiguation: `workload-<app>` vs `software-<app>` (MANDATORY)

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

### Growing the skills every session (generic vs specific routing)

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

### Redaction gate (MANDATORY)

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

### Confirmation gate (MANDATORY)

Agents must NOT self-write skills, lesson files, agent definitions, or MCP
tools. Instead, PROPOSE each update back to the main thread (target skill/tool
+ what you did / symptom → root cause → exact content). The main thread confirms
the observation with the user, and only then is it persisted. This keeps
incorrect diagnoses out of the shared institutional memory while still capturing
learning aggressively.

### Syncing staged annotation lessons back to the repo
`session_ml_append_lesson` stages new entries in a workspace-local copy of
`dftracer-annotation-lessons/LESSONS_LOG.md` that nothing else reads back. `session_lessons_sync_preview` diffs it
against the source repo's copy (read-only; no git or network when a local checkout exists).
`session_lessons_sync_pr(confirm=True)` branches, commits, pushes and opens a PR against the dftracer-agents repo;
it is a visible, hard-to-reverse action on a shared repository: preview first, and call it only after the user
approved, with GITHUB_TOKEN/GH_TOKEN set. Staged text is anonymized at the read boundary.

## Constraints
- Confirmation gate: agents propose lesson/skill/agent/tool updates to the main thread; nothing is persisted without
  the user's sign-off. Then `dftracer_lessons_check(entry, existing_text)` must pass before the write.
- Redaction gate: never write usernames, emails, absolute user paths, flux job ids, session UUIDs or hostnames into a
  lesson; `privacy_scan` / `privacy_redact` verify ([[dftracer_privacy]]).
- `session_lessons_sync_pr` refuses to run on a local checkout with unrelated uncommitted changes, and without confirm=True.
- Keep entries cumulative: never delete old ones.

## Corner cases

### From dftracer-pitfalls

### Annotation Pitfalls (lessons learned)

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
`dftracer_lessons_check` failures:
- `shape` -> rewrite the entry as `- [L-<8 hex>] <symptom> -> <root cause> -> <fix> (<date>)` or as a block with
  context, error (exact message), root_cause, fix (exact steps) and tags.
- `privacy` -> replace identifiers with `$USER`, `$PROJECT_ROOT`, `$LUSTRE_ROOT`, `$HOME`, `<flux-jobid>`, `<uuid>`,
  `<system><node>`; keep the lesson, drop the provenance.
- `new` -> the lesson is already recorded: refine the existing entry instead of adding a duplicate.
- `inconclusive` -> nothing to check: pass the entry text.
`session_lessons_sync_pr` errors: "confirm must be explicitly set" -> preview and ask; "GITHUB_TOKEN or GH_TOKEN" ->
the user sets it; "unrelated uncommitted changes" -> the user commits or stashes them first.

## Self-heal
- A sync tool is wrong: fix `mcp_tools/tools/session/lessons_sync.py` (dftracer-agents) or `capability_update` this
  capability, and ask for an MCP restart.
- An annotation pitfall keeps recurring although recorded: turn it into a deterministic check of the annotation
  validator (the annotate/validate capabilities), not another prose entry.

## Self-learn
- What is explored here becomes a capability: a workload's lessons go into `workload_<app>`, a system's into
  `system_<machine>`, a library's into `software_<lib>`, a tool's into `tools_<x>`; a generic rule reaches every agent
  (shared section + every capability that needs it); see capability_builder.
- Every fix during annotation is appended immediately; at session end scan the annotation log for patterns not yet covered.

## What works
- Before any build/install/configure/annotate step: read the lessons cache, match `tags`/`error` to the task, apply the
  recorded fix proactively (process from dftracer-lessons).
- Patch the build system BEFORE annotating (include path, `-L... -Wl,-rpath,...`, `-ldftracer_core -lcpp-logger -lbrahma`),
  annotate MPI-IO backends last and one function at a time, build after every function (dftracer-pitfalls, recorded by 2026-07-30).
