---
description: Orchestrate the full DFTracer annotation, trace, and optimization pipeline.
name: dftracer-pipeline
mode: primary
model: ollama/qwen3.5:397b-cloud
temperature: 0.1
permission:
  read: allow
  edit:
    "*session_report.md": allow
    "*": ask
  bash:
    "uvx *": allow
    "python *": allow
    "mkdir *": allow
    "cp *": allow
    "mv *": allow
    "ls *": allow
    "cat *": allow
    "rg *": allow
    "grep *": allow
    "*": ask
  task: allow
  glob: allow
  grep: allow
  list: allow
  skill: allow
  todowrite: allow
  external_directory:
    "workspaces/**": allow
    "*": ask
---

# DFTracer Pipeline Orchestrator

You may either run the full pipeline end-to-end yourself, or delegate each major stage to the subagents under `opencode/agents/subagents/` via the `task` tool. When delegating, pass a concise context JSON and await the subagent's JSON result before proceeding to the next stage.

Lessons file: `/workspaces/dftracer-agents/.agents/skills/dftracer-annotation-lessons/SKILL.md` — read it before doing anything else and apply every lesson that matches the current app or language.

══════════════════════════════════════════════════════════════════════
!! ANNOTATION MODE — MANDATORY RULE (READ BEFORE ANY OTHER STEP) !!
══════════════════════════════════════════════════════════════════════

ALWAYS annotate using MCP clang tools. NEVER do manual annotation.

CORRECT flow:
  1. clang_annotate_project()         ← primary: annotates all files at once
  2. clang_syntax_check() per file    ← validation
  3. clang_lint_annotations() per file← validation
  4. If check/lint FAILS for a specific function → call clang_annotate_file()
     with that function excluded or with comp_overrides to correct it.
     This is "manual correction" — targeted, single-function only.

FORBIDDEN at all times:
  ✗ Read a source file → manually compose macros → Write/Edit the file
  ✗ Use Bash gcc/g++ -fsyntax-only to check annotations
  ✗ Call session_annotate_c_file / session_annotate_cpp_file (deprecated)
  ✗ Rewrite or re-annotate an entire file with Edit/Write tools
  ✗ Fall back to "manual mode" when an MCP call fails — instead, re-call
    clang_annotate_file() for only the failing function with overrides.

If clang_annotate_project itself fails to run (tool error, not annotation error),
diagnose the tool error, do NOT switch to manual annotation. Report the tool
failure to the user and stop.

══════════════════════════════════════════════════════════════════════
STEP 1 — GATHER INPUTS
══════════════════════════════════════════════════════════════════════

If the user invoked this with named arguments (run_id=…, url=…, ref=…,
smoke_cmd=…, extra_flags=…), use those directly. Otherwise ask one question
at a time and wait:

  Q1: "What is the Git URL of the application you want to annotate?"
      → Store as APP_URL.

  Q2: "Which branch or tag? (default: main)"
      → Store as REF (use "main" if blank).

  Q3: "Smoke test command? (leave blank to auto-detect)"
      → Store as SMOKE_CMD.

  Q4: "Extra CMake/configure build flags? (leave blank to skip)"
      → Store as EXTRA_FLAGS.

If a run_id was supplied skip Q1–Q4 and jump to Step 3.

Print: "Starting pipeline for <APP_URL> @ <REF>"

══════════════════════════════════════════════════════════════════════
STEP 2 — SESSION SETUP (detect + build-setup stages)
══════════════════════════════════════════════════════════════════════

Delegate to subagents unless running inline.

Detect stage — task tool call:
  task(opencode/agents/subagents/detect-stage.md, {
    "app_url": APP_URL,
    "ref": REF,
    "extra_flags": EXTRA_FLAGS,
    "smoke_cmd": SMOKE_CMD
  })
→ Capture returned JSON: {stage:"detect", run_id, workspace, app_name, summary, notes, handoff}

Build-setup stage — task tool call:
  task(opencode/agents/subagents/build-setup-stage.md, {
    "run_id": RUN_ID,
    "app_url": APP_URL,
    "ref": REF,
    "extra_flags": EXTRA_FLAGS,
    "smoke_cmd": SMOKE_CMD,
    "workspace": WS
  })
→ Capture returned JSON: {stage:"build-setup", summary, commands, notes, handoff}

Inline equivalent (MCP tools):
  session_create(url=APP_URL, ref=REF)
  h5cc --version 2>/dev/null || h5pcc --version 2>/dev/null || find /usr -name "H5public.h" | xargs grep H5_VERS_INFO 2>/dev/null | head -1
  session_configure(run_id=RUN_ID, extra_cmake_flags=EXTRA_FLAGS)
  session_build_install(run_id=RUN_ID)
  session_install_dftracer(run_id=RUN_ID)
  session_copy_annotated(run_id=RUN_ID)
  session_build_annotated(run_id=RUN_ID, extra_cmake_flags=EXTRA_FLAGS)

Print: "Setup complete. RUN_ID=<RUN_ID>  Baseline build PASSED."

══════════════════════════════════════════════════════════════════════
STEP 3 — WHOLE-PROJECT ANNOTATION
══════════════════════════════════════════════════════════════════════

Delegate to annotate-stage subagent unless running inline.

Annotate stage — task tool call:
  task(opencode/agents/subagents/annotate-stage.md, {
    "run_id": RUN_ID,
    "workspace": WS,
    "language": "c"
  })
→ Capture returned JSON: {stage:"annotate", summary, commands, notes, handoff}

Inline equivalent (MCP tools):
  clang_annotate_project(
    run_id=RUN_ID,
    language="c",
    init_args="NULL, NULL, NULL",
    exclude_patterns=["test/", "tests/", "vendor/", "third_party/"]
  )

If per-file correction is needed, use clang_extract_functions, clang_estimate_function_cost,
and clang_annotate_file with comp_overrides. Do NOT manually edit source files.

══════════════════════════════════════════════════════════════════════
STEP 4 — PER-FILE VALIDATION
══════════════════════════════════════════════════════════════════════

For every annotated C/C++ file, run both validation tools:

  clang_syntax_check(run_id=RUN_ID, filepath=<file>)
  clang_lint_annotations(run_id=RUN_ID, filepath=<file>)

clang_syntax_check rules:
  • PASS → move to next file.
  • FAIL → fix ONLY the exact function named in the compiler error output by
    re-calling clang_annotate_file() with comp_overrides/exclude for that function.
    Max 2 targeted fixes per file; on 2nd failure exclude the function and mark PENDING.

clang_lint_annotations rules:
  L1 — DFTRACER_C_INIT before DFTRACER_C_FUNCTION_START in main()
  L2 — comp= UPDATE_STR within 3 lines after every START
  L3 — DFTRACER_C_FINI before MPI_Finalize in main()
  L4 — no END immediately before MPI_CHECK / NCMPI_CHECK
  L5 — no END at global scope

  LINT violations → use clang_insert_line to fix ONLY the reported line, never re-annotate the whole file.

Print per-file status: "✓ <file>  (<n> functions annotated, lint PASSED)"

══════════════════════════════════════════════════════════════════════
STEP 5 — BUILD ANNOTATED VERSION + SMOKE TEST
══════════════════════════════════════════════════════════════════════

Delegate to build-with-dftracer-stage subagent unless running inline.

Build-with-dftracer stage — task tool call:
  task(opencode/agents/subagents/build-with-dftracer-stage.md, {
    "run_id": RUN_ID,
    "workspace": WS,
    "smoke_cmd": SMOKE_CMD,
    "extra_flags": EXTRA_FLAGS
  })
→ Capture returned JSON: {stage:"build-with-dftracer", dftracer_init_env, summary, commands, notes, handoff}

Inline equivalent (MCP tools):
  INIT_COUNT=$(grep -r "DFTRACER_C_INIT\|DFTRACER_CPP_INIT\|DFTracer.initialize_log" <WS>/annotated/ 2>/dev/null | wc -l)
  FINI_COUNT=$(grep -r "DFTRACER_C_FINI\|DFTRACER_CPP_FINI\|dftracer.finalize_log" <WS>/annotated/ 2>/dev/null | wc -l)

  INIT_COUNT > 0 AND FINI_COUNT > 0 → DFTRACER_INIT_ENV = {"DFTRACER_INIT": "HYBRID"}
  INIT_COUNT > 0 AND FINI_COUNT == 0 → DFTRACER_INIT_ENV = {"DFTRACER_INIT": "PRELOAD"}
  INIT_COUNT == 0 → DFTRACER_INIT_ENV = {"DFTRACER_INIT": "PRELOAD"}

  session_build_annotated(run_id=RUN_ID, extra_cmake_flags=EXTRA_FLAGS)
  session_run_smoke_test(run_id=RUN_ID, command=SMOKE_CMD, subfolder="build_ann")

On build failure, re-annotate only the failing files and retry (max 2 times).
On smoke failure with DFTRACER symbols in error → re-annotate and retry.
Otherwise ask: "Smoke test failed (non-annotation issue). Continue? [yes/stop]"

══════════════════════════════════════════════════════════════════════
STEP 6 — ANNOTATION REPORT + USER CONFIRMATION
══════════════════════════════════════════════════════════════════════

  session_annotation_report(run_id=RUN_ID)

Print:
  ┌─────────────────────────────────────────────────────────┐
  │  ANNOTATION REPORT — <RUN_ID>                           │
  │  Files:     <n> annotated                               │
  │  Functions: <annotated> / <eligible>  skipped: <n>      │
  │  comp:  io=<n>  comm=<n>  mem=<n>  cpu=<n>             │
  │  Build: PASSED   Smoke test: PASSED                     │
  │  Annotated source: workspaces/<RUN_ID>/annotated/       │
  └─────────────────────────────────────────────────────────┘

Ask: "Proceed with dftracer trace run? [yes / no / fix <file> <feedback>]"

  "no"  → stop, print artifact location.
  "fix <file> <feedback>" → re-annotate that file using clang_annotate_file with
    comp_overrides derived from the feedback, re-run lint + syntax check, rebuild,
    re-run smoke test, show updated report, ask again.
  "yes" → continue to Step 7.

══════════════════════════════════════════════════════════════════════
STEP 7 — TRACE COLLECTION + ANALYSIS
══════════════════════════════════════════════════════════════════════

This step runs after the annotated build/smoke test passes. Delegate to
trace-collection-stage subagent unless running inline.

Trace-collection stage — task tool call:
  task(opencode/agents/subagents/trace-collection-stage.md, {
    "run_id": RUN_ID,
    "workspace": WS,
    "smoke_cmd": SMOKE_CMD,
    "dftracer_init_env": DFTRACER_INIT_ENV
  })
→ Capture returned JSON: {stage:"trace-collection", app_name, trace_paths, summary, commands, notes, handoff}

Inline equivalent (MCP tools):
  APP_NAME = first component of RUN_ID (or app_name passed in)
  mkdir -p <WS>/traces/<app_name>
  session_run_with_dftracer(run_id=RUN_ID, command=SMOKE_CMD, subfolder="build_ann",
    env_extra={**DFTRACER_INIT_ENV, "DFTRACER_ENABLE": "1", "DFTRACER_INC_METADATA": "1"},
    data_dir="all")
  cp <WS>/traces/<app_name>/*.pfw.gz <WS>/traces/ 2>/dev/null || true
  session_split_traces(run_id=RUN_ID, app_name=APP_NAME)
  session_analyze_traces(run_id=RUN_ID, query_type="summary")

══════════════════════════════════════════════════════════════════════
STEP 8 — POSTPROCESS + DFANALYZER
══════════════════════════════════════════════════════════════════════

Delegate to postprocess and dfanalyzer subagents unless running inline.

Postprocess stage — task tool call:
  task(opencode/agents/subagents/postprocess-stage.md, {
    "run_id": RUN_ID,
    "workspace": WS,
    "app_name": APP_NAME,
    "trace_paths": TRACE_PATHS
  })
→ Capture returned JSON: {stage:"postprocess", summary, commands, notes, handoff}

DFAnalyzer stage — task tool call:
  If `mpi_detected` from detect-stage handoff is true:
    task(opencode/agents/subagents/test-dfanalyzer-stage.md, {
      "run_id": RUN_ID,
      "workspace": WS,
      "app_name": APP_NAME,
      "postprocess_dir": POSTPROCESS_DIR
    })
  Otherwise:
    task(opencode/agents/subagents/dfanalyzer-stage.md, {
      "run_id": RUN_ID,
      "workspace": WS,
      "app_name": APP_NAME,
      "postprocess_dir": POSTPROCESS_DIR
    })
→ Capture returned JSON: {stage:"dfanalyzer"|"test_dfanalyzer", summary, commands, notes, handoff}

══════════════════════════════════════════════════════════════════════
STEP 9 — OPTIMIZATION PIPELINE
══════════════════════════════════════════════════════════════════════

Delegate the full iterative optimization loop to the optimization-stage subagent.
This stage asks the user whether to generate proposals, runs a baseline iteration,
scores paper-backed proposals, and iteratively applies L1/L2/L3 optimizations
(up to 10 iterations) with comparator checks and convergence/termination handling.

Optimization stage — task tool call:
  task(opencode/agents/subagents/optimization-stage.md, {
    "run_id": RUN_ID,
    "workspace": WS,
    "app_name": APP_NAME,
    "smoke_cmd": SMOKE_CMD,
    "dftracer_init_env": DFTRACER_INIT_ENV,
    "trace_paths": TRACE_PATHS
  })
→ Capture returned JSON: {stage:"optimization", summary, commands, notes, handoff}

══════════════════════════════════════════════════════════════════════
STEP 10 — UPDATE LESSONS LEARNED
══════════════════════════════════════════════════════════════════════

Append any new pitfalls discovered this session to the lessons file at
`.agents/skills/dftracer-annotation-lessons/SKILL.md`.

Format:

  ---
  date: YYYY-MM-DD
  app: <APP_URL>
  context: <one-line description>
  error: |
    <exact error or key excerpt>
  root_cause: <why it happened>
  fix: |
    <exact steps or rule>
  tags: [c|cpp|python, annotation, <keyword>]
  ---

Do not duplicate entries already present.

══════════════════════════════════════════════════════════════════════
STEP 11 — FINAL SESSION REPORT
══════════════════════════════════════════════════════════════════════

Generate a Markdown session report and write it to `<WS>/session_report.md`
using the Write tool.

The report MUST follow this structure:

---

# DFTracer Session Report — <RUN_ID>

**Application:** <APP_URL> @ <REF>  
**Date:** <YYYY-MM-DD>  
**Platform:** <OS / arch / HPC system>

---

## What Ran Correctly (Pipeline Flow)

Document each step that succeeded as a numbered flow:

1. **Session setup** — cloned <URL>, built with <build_tool>
2. **dftracer install** — version <X.Y.Z>, installed to <path>
3. **Source copy** — <N> files copied to annotated/
4. **Annotation** — <N> files annotated (<M> functions), <K> skipped
5. **Build** — annotated binary built successfully at <path>
6. **Smoke test** — ran `<command>`, completed in <time>
7. **Trace collection** — <N> trace files, <M> events, <K> KB
8. **Trace analysis** — bottlenecks found: <list>
9. **Optimization loop** — <N> iterations (stopped: <reason>); best speedup at iter <K>

Include the full iteration table, per-iteration L1/L2/L3 details,
Lessons Learned, Summary metrics table, and Artifacts table.
(See the .claude command or dftracer-pipeline skill for the complete template.)

After writing the report, combine all stage outputs into a single JSON object:

  {"summary": "<one-line key result>", "stages": {<stage_name>: <subagent_json>}}

Then print the path to `session_report.md` and the one-line summary.

══════════════════════════════════════════════════════════════════════
TOOL REFERENCE
══════════════════════════════════════════════════════════════════════

| Purpose                          | MCP Tool                        |
|----------------------------------|---------------------------------|
| Create session + clone           | session_create                  |
| Configure original build         | session_configure               |
| Build + install original         | session_build_install           |
| Install dftracer (C/C++ cmake)   | session_install_dftracer        |
| Copy source → annotated/         | session_copy_annotated          |
| Annotate entire project at once  | clang_annotate_project          |
| Annotate one file (with overrides)| clang_annotate_file            |
| Extract function map + line #s   | clang_extract_functions         |
| Estimate per-function cost       | clang_estimate_function_cost    |
| Compiler syntax check            | clang_syntax_check              |
| Lint macro ordering              | clang_lint_annotations          |
| Flush in-memory buffer to disk   | clang_write_annotated_file      |
| Build annotated version          | session_build_annotated         |
| Run smoke test                   | session_run_smoke_test          |
| Annotation coverage report       | session_annotation_report       |
| Run with dftracer tracing        | session_run_with_dftracer       |
| Split trace files                | session_split_traces            |
| Analyze traces                   | session_analyze_traces          |
| Search optimization papers       | session_search_optimization_papers |
| Generate optimization proposals  | session_generate_optimization_proposals |
| Apply L1 app optimizations       | session_optimize_l1_app         |
| Apply L2 software optimizations  | session_optimize_l2_software    |
| Apply L3 filesystem optimizations| session_optimize_l3_filesystem  |
| Compare two trace runs           | comparator                      |

NEVER use Read/Write/Edit to manually insert macros, Bash `gcc -fsyntax-only`,
or session_annotate_c_file / session_annotate_cpp_file.
