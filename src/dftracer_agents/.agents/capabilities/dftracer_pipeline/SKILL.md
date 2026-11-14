---
name: dftracer_pipeline
description: Plan, route and run the full dftracer pipeline (system detect, session setup, build app, install dftracer, annotate, build+smoke, trace, analyze, diagnose, optimize, report, privacy guard). Load FIRST for any full pipeline request ("annotate and trace <app>", "run the dftracer pipeline", "plan the run"), for session_run_pipeline (pauses at step 8 with awaiting_confirmation, resume with annotation_confirmed=True), when pipeline_plan.md is missing or a step agent cannot find its '## STEP N: <agent>' section, "Step N failed" from session_run_pipeline, "Workspace not found for run_id", "name 'smoke_cmd' is not defined" on resume, empty 0-byte .pfw traces, DataLoader worker SIGSEGV/SIGABRT under dftracer, or dfanalyzer posix vs dlio preset choice.
---
Cross-references: [[dftracer_system]], [[dftracer_pipeline_run]], [[pipeline_control]], [[run_planner]], [[capability_builder]]

# dftracer_pipeline

Converted from dftracer-agents: skills dftracer-pipeline (+ dataloader_crash_debugging.md), dftracer-planning,
dftracer-project-router (rules, permissions, pitfalls, lessons), the pipeline entries of dftracer-lessons and
dftracer-pitfalls, and the agents dftracer-pipeline-planner and dftracer-project-router. The full original
procedure is kept verbatim under "Reference" at the end.

## Rules
- The session exists BEFORE planning. The planner is given a run_id (session_status + session_get_run_paths first);
  if it has none it stops and asks. It never creates a session itself.
- Plan first: render the skeleton with dftracer_pipeline_plan_template(run_id, mode), fill every '<fill: ...>',
  write it with session_write_file(run_id, subfolder=".", filepath="pipeline_plan.md") (fall back to
  subfolder="scripts" and say so), validate with dftracer_pipeline_check(plan_md=..., run_id=...), and hand back
  ONLY: run_id, the plan path, and the one-line 'DISPATCH ORDER:' list.
- One executor agent per step, heading exactly '## STEP N: <executor-agent>' so the step agent greps its own
  section. Each section is self-contained (the step agent starts cold): tools, exact inputs (paths, commands, flags),
  expected artifact (run_id, trace dir, bottleneck list), gotchas, decision points that need a human
  (e.g. "annotation scope: 51 files - confirm before annotating").
- Perf stage table (stage -> executor, level): 0 system detection -> dftracer-system-detect (level_1);
  1 session setup -> dftracer-session-setup (level_1); 2 build original app -> dftracer-build-app (level_1);
  3 install/build dftracer ONLY after stage 2 succeeded, against the dependencies stage 2 resolved (compiler, MPI,
  venv, HDF5/ROCm versions; only the tracing layers the user asked for) -> dftracer-build-dftracer (level_1);
  4 annotation scoping + annotation + validation -> dftracer-annotator and the file-type annotators (level_2);
  5 build annotated + smoke -> dftracer-build-smoke (level_3); 6 best-case trace run + split -> dftracer-tracer
  (level_1); 7 analyze + diagnose -> dftracer-analyzer then dftracer-diagnoser (level_3; preset posix vs dlio);
  8 optimization loop -> dftracer-optimizer (level_4 in the source table: the largest model);
  9 final report -> dftracer-report (level_2); 10 privacy scan/redact -> dftracer-privacy-guard (level_1).
- Stages 9 and 10 are ALWAYS in the plan, last, in that order, even for a failed or partial pipeline.
  session_final_report must return pdf.generated, completeness.ok and readme_check.ok all true before stage 9 is
  done; on a partial pass dftracer-report fixes the named gap and calls again. privacy_scan must include
  final_report/ explicitly (it is not in the default scan set) and report clean.
- Provenance mode REPLACES perf stages 6-8: no PAPI, variorum/power, GPU profilers, dftracer_service node
  counters, analyzer, diagnoser or optimizer. Order: system detect, session setup, build app; plain run of the
  original app (dftracer-tracer, tracing disabled; outputs in <WS>/dataset/<run>/); dftracer-provenance-discover
  (writes provenance/discovery.md, returns questions); USER DECISION (artifact(s), entity types, granularity ->
  provenance_write_spec; a hard human gate, the plan says the pipeline pauses there); dftracer-build-dftracer
  (data-centric); dftracer-provenance-annotate (provenance_validate passes); dftracer-build-smoke then
  dftracer-tracer with DFTRACER_INC_METADATA=1, traces -> <WS>/provenance/traces/, no service daemon;
  dftracer-provenance-graph (health gate, loop to annotate on gaps); report; privacy guard.
- Routing (dftracer-project-router): route each stage to the narrowest single-purpose subagent; session setup /
  install -> dftracer-session-setup; annotation -> dftracer-annotator or the file-type annotators; build and smoke
  -> dftracer-build-smoke; best-case trace -> dftracer-tracer; analysis -> dftracer-analyzer; diagnosis ->
  dftracer-diagnoser; optimization loop -> dftracer-optimizer. The router never performs a stage job itself.
- Model policy: smallest model (level_1, Haiku) for deterministic tool recipes; level_2 (Sonnet) when output must
  be interpreted or one path chosen among several; level_3 (Opus) only for synthesis-heavy optimization or diagnosis.
  Escalate on reasoning complexity, never by habit.
- Tool-first, in this order: session_status, session_list_runs, session_get_run_paths (NEVER hand-build paths),
  system_detect, graph_query(mode="docs"), skill_search, docs_search, list_presets. Tools unavailable -> stop and ask
  the user to start the dftracer MCP server; a tool that errors is a REPAIR task (fix it, re-call it); never
  hand-roll what a tool does with Bash, and never report success after hand-rolling. Stopping is acceptable.
- Step tracking: one task per STEP, created up front and named for the step ("STEP 3: build app"); exactly one in
  progress; profile_step_begin / profile_step_end around each step with the same name; a retry closes the attempt
  with the real error and reopens with the SAME step string; never status="ok" for a step that did not succeed;
  never call profile_bind from a step agent (orchestrator only).
- Planning and progress (dftracer-planning): for more than two steps write the numbered plan first; after each step
  report what was done and what is next; surface blockers immediately with the remaining steps. For steps over
  ~10 s announce before the call and report after (session_create 10-60 s; session_configure 10-60 s;
  session_build_install 30 s-10 min; session_run_smoke_test 5-120 s; session_install_dftracer 1-10 min;
  session_build_annotated per attempt 30 s-10 min; session_run_with_dftracer 5-120 s; session_split_traces 5-60 s;
  session_analyze_traces 10-120 s). For builds show the FIRST compiler error, not the cascade.
- Context economy: delegate heavy stages (annotation pass, optimization iteration) to a sub-agent that returns a
  compact summary; prefer compact status tools (session_status, session_validate_structure) over re-reading
  artifacts/*.log; locate with the knowledge graph before reading files.
- Ask early, once per session, during planning: which of the app's workloads/configs/sub-apps to focus on (offer
  the discovered list; "all" is valid only as the user's answer); for runs needing nodes, whether to use an existing
  allocation (flux proxy <JOBID> bash <wrapper>.sh, check remaining time) or spawn one; for DL runs, the time budget
  (calibrate seconds/epoch on the baseline, fix the epoch count for every variant).
- Pipeline eagerly: start the next stage on the units that are ready; track pending units; reports say which units
  are included and which are pending.
- Environment consistency: the application defines the environment (its scripts/install-<system>.sh,
  scripts/<app>-<system>.job, pyproject.toml, reused verbatim); install env == run env; dftracer in the SAME
  venv/script as the app; CC/CXX bound to the app's MPI; MPI (and HDF5 only if the app uses it) passed explicitly.
  A zero exit code does not mean tracing worked: verify `import dftracer.dftracer` and a NON-EMPTY .pfw.
- Every run-producing step ends with session_capture_run_record(run_id, run_name, prev_run_name, source_path,
  run_script, run_log, param_files, notes) (and session_snapshot_run_source when the run has its own source tree);
  backfilling later does not work because builds/params are overwritten in place.
- All logs go to <WS>/artifacts/<step>_<what>.log; tmp/ only holds wrapper scripts and scratch inputs.
- Annotation is finished only when validate_annotations(run_id, language) passes with zero findings and the
  matching dftracer-validate-<lang> agent agrees, also after any hand edit or tool failure.

## Constraints
- Operate only inside the current session workspace and its canonical paths (baseline/, annotated/, traces/,
  opt<n>/, artifacts/, tmp/, dataset/); never invent paths outside the session-owned tree; never sudo; never build or
  run from the project root.
- The planner and router never build, annotate, trace or optimize; a planner that also builds or traces becomes a
  bottleneck, and routing with too many tools in one agent wastes context.
- Annotation is done ONLY with the MCP annotation tools (clang_annotate_project / clang_annotate_file /
  clang_syntax_check / clang_lint_annotations; python_* for Python): never compose macros by hand with Edit/Write,
  never `gcc -fsyntax-only` from Bash, never the deprecated session_annotate_c_file / session_annotate_cpp_file.
- Never set DFTRACER_INIT=0 (disables POSIX tracing) or DFTRACER_INIT=1 when the source has explicit
  DFTRACER_C_INIT; values are case-sensitive (FUNCTION, HYBRID). PRELOAD-only mode is never used.
- No self-writing of skills, lessons, agent definitions or tools: propose (target -> symptom/what you did -> root
  cause -> exact content) to the main thread, which confirms with the user. Redact before you persist: no usernames,
  emails, absolute user paths, flux job ids, session UUIDs or node hostnames ($USER, $PROJECT_ROOT, $LUSTRE_ROOT,
  <flux-jobid>, <uuid>, <system><node>).
- When a tool fix needs an MCP server restart, STOP, name the tool and wait; never reproduce its job by hand.

## Corner cases
- session_run_pipeline returns awaiting_confirmation=True after step 8: show the annotation report, wait for the
  user, then call again with annotation_confirmed=True and the same run_id (steps 1-8 are skipped).
- The resume path (annotation_confirmed=True) of session_run_pipeline reads `smoke_cmd` and `src`, which only the
  first call defines, so it raises NameError once the workspace exists (moved unchanged; known defect). Continue
  through the stage tools (session_install_dftracer, session_build_annotated, session_run_with_dftracer,
  session_split_traces, session_analyze_traces) or repair the tool.
- session_run_pipeline's step 8.5 installs the dftracer prerelease with its prebuilt modules and composes the
  features detection asked for (mpi, hdf5, papi, hip, hwloc, cuda) with the application's env script; it fails if
  compose reports anything missing.
- HDF5 must be >= 1.14 (page buffer with MPIO VFD, async VOL, collective metadata flush); with 1.10/1.12 build 1.14
  into the session and pass -DHDF5_DIR to every cmake step.
- Python apps: dftracer and the app share the same venv (ws/install/); session_install_dftracer enforces it.
- Python functions never decorated: @staticmethod (use a `with DFTracerFn(...)` / `with dft_ai.compute.backward()`
  region instead of @log_static), @numba.njit/@numba.jit/@cuda.jit and @torch.jit.script (TypeError: unsupported
  callable), Dataset.__len__ (called O(10000+) times per epoch: buffer overflow, SIGABRT/SIGSEGV in workers).
- DataLoader worker segfault on cleanup or in finalize (flockfile/BufferManager, with HIP tracing on): see the
  DataLoader reference below; skip the decorator, the annotated callers still capture the time.
- dfanalyzer preset: dlio when the source imports torch, tensorflow, jax, keras, flax, mxnet, horovod, deepspeed,
  megatron, FSDP, dali, dlio_benchmark or lightning (_detect_analyzer_preset), else posix; pass
  analyzer/preset=<x> when calling analyze by hand.
- Empty (0-byte) traces although DFTRACER_ENABLE=1 and "DFTracerCore::initialize" printed: finalize never ran.
  Finalize after MPI_Finalize / mpi4py atexit, an unhandled exception, or torch destroy_process_group before
  finalize -> wrap the benchmark in try/finally and finalize before MPI teardown. Missing DataLoader worker I/O ->
  workers died uncleanly (SIGKILL, OOM, exception): reduce num_workers or fix the worker; do not switch to HYBRID.
- On LLNL systems traces go to Lustre (session_run_with_dftracer routes there and symlinks <WS>/traces/); flux
  proxy smoke commands are wrapped by the tool in <ws>/tmp/run_smoke_test.sh; never inline
  `bash -c "module load ..."` through flux proxy, and pass env with --env (exports do not propagate).
- MPI as root (containers): OMPI_ALLOW_RUN_AS_ROOT=1 and OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1.
- Smoke binary not found (rc 127): use the full absolute path (find it outside source/).
- Binary links but `libdftracer_core.so: cannot open shared object file`: add -Wl,-rpath,<prefix>/lib or
  LD_LIBRARY_PATH.
- Long `flux proxy` in the foreground: the Bash tool caps at ~10 min and killing the proxy kills the job; run it
  in the background and poll. 8-node pbatch may sit in SCHED; pdebug usually schedules at once.
- Optimization: bottlenecks are addressed I/O -> communication -> memory -> compute (severity only breaks ties);
  every proposal carries a paper citation scored bottleneck(0-50)+system(0-30)+recency(0-20); top < 20 -> another
  search pass; DL workloads always evaluate dataloader/epoch time and filesystem bandwidth. Loop at most 10
  iterations; stop on EXHAUSTED, CONVERGED, REGRESSED (> 5% worse per the comparator; roll back) or MAX_ITERS.
- "Do-less" levers (fewer epochs, larger checkpoint_interval, smaller dataset) are not speedups; check total bytes.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
dftracer_pipeline_check names the failed check; repair the input (or the tool), then validate again:
- plan_header / plan_run_id / overview -> re-render with dftracer_pipeline_plan_template(run_id) for the run_id
  from session_status and refill; never invent a run_id.
- has_steps / step_numbers -> one '## STEP N: <agent>' per stage, numbered contiguously from 0 or 1.
- known_executors -> use the exact subagent name from the stage table.
- stage_order -> reorder; in particular dftracer-build-dftracer goes after dftracer-build-app (dftracer is built
  against the dependencies the app build resolved), annotate before build-smoke, tracer before analyzer.
- report_then_privacy_last -> append STEP dftracer-report then STEP dftracer-privacy-guard.
- provenance_no_perf_stages / provenance_user_gate -> drop analyzer/diagnoser/optimizer; add the USER DECISION
  step with provenance_write_spec.
- steps_self_contained / no_unfilled_placeholders -> fill each section with tools, inputs, commands, artifacts.
- dispatch_order -> rewrite '## DISPATCH ORDER' from the step headings.
- result_status -> follow the per-step fix in the check (1 clone, 3 configure, 4 make, 8.5 dftracer
  install/compose, 9 annotated build, 10 smoke under dftracer; "Workspace not found" = wrong run_id), then call
  session_run_pipeline again (or the single stage tool).
- resume_path -> continue with the stage tools or repair session_run_pipeline (capability_update).
- annotation_report / step_order / step_durations -> rerun the pipeline in a clean session; report the defect.
Attempts follow measured progress: the same failure twice stops the loop for the user.

## Self-heal
- If session_run_pipeline or a stage tool is wrong, repair it (capability_update on this capability, or the
  owning capability), restart if needed, re-call it; never work around it in shell.
- Keep the router thin and deterministic: stage-specific heuristics belong in the stage capability, not here.

## Self-learn
- What is explored here becomes a capability: see capability_builder. A new stage, executor, routing rule or
  pipeline mode updates this capability's stage table and validator; workload/system/software facts met during a
  run go to workload_<app> / system_<machine> / software_<lib> (a traced scientific workload is workload_<app>; a
  tool/service/DL-training app being instrumented is software_<app>; ask when ambiguous).
- Every user answer or correction is a lesson (lesson_add) on the capability it concerns; a GENERIC rule must reach
  every agent and skill, not one corner of one skill.
- While long jobs run, record lessons as they are resolved instead of at the end (context compaction loses them).

## What works
- (2026-07-10) Planner flow: session exists -> session_status/session_get_run_paths -> system_detect -> ask which
  workloads/configs -> pipeline_plan.md with '## STEP N: <agent>' sections + DISPATCH ORDER -> short summary to the
  main thread, which dispatches each step agent to load its own section.
- (2026-06-20) C init args: DFTRACER_C_INIT(NULL, NULL, NULL); clang_annotate_project(init_args="NULL, NULL, NULL").
- session_run_pipeline phase 1 (steps 1-8: clone, detect, configure, build+install, smoke, copy annotated, patch
  build, annotate) then pause; phase 2 (8.5 dftracer prerelease + compose, 9-10 annotated build + run with
  DFTRACER_ENABLE=1 DFTRACER_INC_METADATA=1 DFTRACER_INIT=FUNCTION with up to 3 automatic fix attempts, 11 split via
  dftracer-utils, 12 dftracer_info summary, 13 dfanalyzer checkpoint + dfdiagnoser) writes step_timings.json.
- Trace runs: DFTRACER_INIT=FUNCTION first (C macros or Python initialize_log/finalize + decorators); HYBRID with
  LD_PRELOAD=<dftracer>/lib/libdftracer_preload.so only if FUNCTION gives an empty trace or crashes; data_dir="all".

## Reference: dftracer-pipeline procedure (carried over verbatim)

Lessons file: /workspaces/dftracer-agents/.agents/skills/dftracer-annotation-lessons/LESSONS_LOG.md
(load the compact rules with skill_load(name="dftracer-annotation-lessons"); load the
accumulated entries with skill_load(name="dftracer-annotation-lessons", file="LESSONS_LOG.md"))

Read the lessons file before doing anything else. Apply every lesson that
matches the current app or language.

══════════════════════════════════════════════════════════════════════
!! STEP TRACKING — MANDATORY (the run must be legible while it runs) !!
══════════════════════════════════════════════════════════════════════

This pipeline has NINE steps. Track them as nine.

  * Create ONE task per STEP, up front, named for that step
    ("STEP 3: build app"). NEVER a single task like "run the pipeline
    steps" covering several of them.
  * Exactly one step in_progress at a time; mark it completed before
    starting the next.
  * Call profile_step_begin before a step's work and profile_step_end
    after it, with the same name. Duration, failures and retries are
    measured from these markers — a step that never calls them did not
    happen as far as any later analysis can tell.
  * When a step fails and you retry it, say so in its task description.
    Nothing else records the retry count.

Left uninstructed this collapses to four generic tasks with STEP 2
through STEP 8 inside one of them. A watcher then sees a single task
in progress for an hour, and the run cannot say which step failed.

══════════════════════════════════════════════════════════════════════
!! TOOL POLICY — MANDATORY (never hand-roll what a tool does) !!
══════════════════════════════════════════════════════════════════════

  * If an MCP tool exists for a job, it is the ONLY way to do that job.
    Never reimplement it with Bash — not even when the tool has just
    failed and the shell command that would work is obvious.
  * A failing MCP tool is a REPAIR TASK. Read the error as a defect
    report, fix the tool under src/dftracer_agents/mcp_tools/tools/,
    then re-call it. The run continues THROUGH the tool.
  * Record the repair: a lesson on the relevant skill, and a memory
    entry when the fact is about the workload rather than the tool.
  * If it cannot be repaired within the step's budget, STOP and report
    the defect. Stopping is acceptable. Hand-rolling it and reporting
    success is not — it hides the defect and reports health the system
    does not have.
  * Bash stays free for what no tool covers: inspecting the tree,
    reading logs, verifying a fix.

Load the `mcp-first` skill before the first build step.

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

  FORBIDDEN at all times (do NOT do these, ever):
    ✗ Read a source file → manually compose macros → Write/Edit the file
    ✗ Use Bash gcc/g++ -fsyntax-only to check annotations
    ✗ Call session_annotate_c_file / session_annotate_cpp_file (deprecated)
    ✗ Rewrite or re-annotate an entire file with Edit/Write tools
    ✗ Fall back to "manual mode" when an MCP call fails — instead, re-call
      clang_annotate_file() for only the failing function with overrides.

  If clang_annotate_project itself fails to run (tool error, not annotation
  error): diagnose the tool error, do NOT switch to manual annotation.
  Report the tool failure to the user and stop.

PYTHON AI/ML ANNOTATION — FUNCTIONS TO NEVER ANNOTATE:

  The following Python patterns must not receive dftracer decorators.
  Remove any auto-placed decorators from them before running:

  ✗ @staticmethod functions must NEVER carry @_dlp.log_static — dftracer's
    decorator passes self as the first positional arg via *args, causing
    "multiple values for argument" errors when the static method has keyword
    parameters.  Instead instrument the body with a CONTEXTUAL region (dft_fn
    and the dft_ai objects are context managers):

        @staticmethod
        def f(...):
            with DFTracerFn("<cat>", name="f"):      # generic
                ...
        @staticmethod
        def backward(...):
            with dft_ai.compute.backward():          # semantic: keep the AI API
                ...

    `python_annotate_file` / `python_annotate_ai_file` now emit this
    automatically; `validate_annotations` flags any surviving @log_static.

  ✗ @numba.njit / @numba.jit / @cuda.jit compiled kernels — numba CPUDispatcher
    objects do not support inspect.getfullargspec; dftracer's log decorator fails
    at decoration time with "TypeError: unsupported callable".

  ✗ @torch.jit.script decorated functions — same reason as numba.

  ✗ __len__ on Dataset subclasses — DataLoader calls __len__ O(10000+) times
    per epoch; annotating it overflows dftracer's C-level event buffer and
    causes SIGABRT in DataLoader workers.  Leave __len__ unannotated.

  DataLoader worker segfault (on cleanup, or during dftracer finalize)? Two
  known root causes and the full gdb/core-dump debugging procedure are in a
  separate reference file — load it only if this crash actually occurs:

    skill_load(name="dftracer-pipeline", file="dataloader_crash_debugging.md")

  For either root cause, the fix is the same: simply skip the decorator; the
  function will still be called normally and its callers (which ARE annotated)
  will capture the timing.

DFANALYZER PRESET SELECTION — DLIO vs POSIX:

  dfanalyzer supports two analysis presets:
    • posix  — generic POSIX I/O workload (default for C/C++/Fortran HPC apps)
    • dlio   — deep learning workload (understands epoch/fetch_data/data_loader/
               checkpoint/compute layers; use for PyTorch/TF/JAX/DALI/horovod apps)

  The pipeline auto-detects the preset via _detect_analyzer_preset():
    • If source code imports any of: torch, tensorflow, jax, keras, flax, mxnet,
      horovod, deepspeed, megatron, FSDP, dali, dlio_benchmark, lightning
      → preset is automatically set to dlio
    • Otherwise → posix

  When calling mcp__dftracer__analyze manually, always pass the correct preset:
    • DL workload:   analyzer/preset=dlio
    • Generic HPC:   analyzer/preset=posix

  The dlio preset produces semantically richer bottleneck names (e.g.
  reader_posix_read_ops_slope instead of posix_read_ops_slope for DataLoader
  workers) and understands training-phase patterns that posix does not.

══════════════════════════════════════════════════════════════════════

SESSION HYGIENE — SKILL UPDATES AND CONTEXT MANAGEMENT:

  While waiting for long-running jobs (smoke test, dftracer run, optimization
  iteration), always use the idle time to update skill files with lessons
  learned so far in the session. Do not wait until the end — capture pitfalls
  as soon as they are resolved so they are not lost to context compaction.

  Protocol:
    1. After any bug fix or new pitfall discovered, immediately update the
       relevant skill file (.claude/commands/*.md) with the lesson.
    2. While a flux job is running and output has not yet appeared, update skills.
    3. If conversation context exceeds ~60%, update all relevant skill files
       with current lessons, then run /compact to free context before continuing.
    4. After /compact, re-read the task list and resume from where you left off.

  Skill files to update when relevant:
    • dftracer-ml-annotate.md  — new Python annotation pitfalls
    • dftracer-pipeline.md     — new pipeline protocol rules
    • flux-alloc.md            — new Flux job management lessons

══════════════════════════════════════════════════════════════════════
STEP 1 — GATHER INPUTS  (if not supplied via arguments)
══════════════════════════════════════════════════════════════════════

If the user invoked this with named arguments (run_id=…, url=…, etc.),
use those directly. Otherwise ask one question at a time and wait:

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

Note: every MCP tool call that runs a pipeline step returns timing fields
``started_at``, ``ended_at``, and ``duration_s`` in its result.  Collect
these into a running ``STEP_TIMINGS`` list as steps complete:

    STEP_TIMINGS = []   # append {step, started_at, ended_at, duration_s} after each step


══════════════════════════════════════════════════════════════════════
STEP 2 — SESSION SETUP  (MCP tools)
══════════════════════════════════════════════════════════════════════

2a. Create session and build the original source:

    session_create(url=APP_URL, ref=REF)
    → store RUN_ID, WS (workspace path)

    STRICT RULE: every step from here on writes only into the paths this
    session owns (baseline/, annotated/, opt<n>/, artifacts/, tmp/,
    dataset/) — never a path a step invents on its own. Get exact paths from
    session_get_run_paths or session.json["paths"], not by hand-building
    strings like "ws/build_ann". See dftracer-cheatsheet S0.

2b. Check HDF5 version before configuring:

    h5cc --version 2>/dev/null || h5pcc --version 2>/dev/null || \
      find /usr -name "H5public.h" | xargs grep H5_VERS_INFO 2>/dev/null | head -1

    REQUIRED: HDF5 ≥ 1.14.x. If the system HDF5 is 1.10.x or 1.12.x:
      - Build HDF5 1.14 from source into <WS>/hdf5_1.14/ (see dftracer-install skill)
      - Add "-DHDF5_DIR=<WS>/hdf5_1.14" to EXTRA_FLAGS for all cmake steps
      - Set HDF5_DIR and LD_LIBRARY_PATH in every subsequent shell command

    HDF5 1.14 unlocks: H5Pset_page_buffer_size with MPIO VFD,
    async VOL (H5Fcreate_async), improved collective metadata flush,
    and the full posix_close_ops_slope fix path.

2c. Configure + build the original source:

    session_configure(run_id=RUN_ID, extra_cmake_flags=EXTRA_FLAGS)
    session_build_install(run_id=RUN_ID)

2d. Install dftracer into the session — ONLY AFTER 2c succeeded. ORDER IS
    MANDATORY: build the original app first, then build dftracer against the
    exact dependencies that app build resolved (same compiler/CC/CXX, same MPI,
    same venv; HDF5/ROCm only if the user wants those tracing layers, and then
    the same version the app linked). Never install dftracer first on guessed
    deps. ``session_install_dftracer`` refuses to run until the app build is
    recorded (pass ``app_built_override=True`` only for hand-built apps).
    Enable only the tracing layers the user asked for (e.g. mpi=True,
    papi/hip/variorum/hdf5=False):

    session_install_dftracer(run_id=RUN_ID)

    On failure → print the cmake/pip error and stop.

    IMPORTANT for Python/AI/ML apps: dftracer and the app MUST share the
    same venv (``ws/install/``).  ``session_install_dftracer`` enforces this
    automatically for ``build_tool=python`` projects — it installs dftracer
    into ``ws/install/`` and never creates a separate ``ws/venv/``.
    If the app venv does not exist yet, it is created by this step.

2d. Copy source to annotated/ workspace:

    session_copy_annotated(run_id=RUN_ID)

2e. Baseline annotated build (no macros yet — verifies the build
    system patch works before any annotation):

    session_build_annotated(run_id=RUN_ID,
      extra_cmake_flags=<same flags as 2b>)

    On failure → show cmake/make errors and stop.

Print: "Setup complete. RUN_ID=<RUN_ID>  Baseline build PASSED."

2f. Validate session structure before annotating anything:

    session_validate_structure(run_id=RUN_ID)

    If clean=false → session_reorganize_structure(run_id=RUN_ID, dry_run=False)
    then re-run session_validate_structure to confirm clean=true before
    proceeding to Step 3. Never annotate into a drifted workspace.


══════════════════════════════════════════════════════════════════════
STEP 3 — WHOLE-PROJECT ANNOTATION  (clang MCP tools)
══════════════════════════════════════════════════════════════════════

The preferred path is a single project-level call that discovers all
C/C++ files, determines entry points, filters trivial functions by AST
cost, and inserts macros in bottom-to-top line order:

    clang_annotate_project(
      run_id      = RUN_ID,
      language    = "c",          # or "cpp" for C++ projects
      init_args   = "NULL, NULL, NULL",
      exclude_patterns = ["test/", "tests/", "vendor/", "third_party/"]
    )

This call:
  • Discovers every .c / .cpp / .cxx / .cc under annotated/
  • Skips files matching exclude_patterns (plus the always-excluded
    /CMakeFiles/, /.git/ paths)
  • Annotates library/inner files first (is_entry=False)
  • Annotates entry-point files last (is_entry=True) so INIT/FINI land
    around main()
  • For each file, internally calls clang_extract_functions to get an
    authoritative function map, then clang_estimate_function_cost per
    function to decide annotate vs. skip (score ≥ 20 or lifecycle rule)
  • Inserts macros in a single in-memory pass (no intermediate writes)
  • Writes each file exactly once via clang_write_annotated_file

Print the result: number of files annotated, functions annotated vs skipped.

##### When to use per-file annotation instead

If clang_annotate_project reports errors for specific files, OR if you
need comp= overrides for particular functions, switch to per-file mode
for those files only:

    # 1. Extract the function map
    clang_extract_functions(run_id=RUN_ID, filepath=<file>)

    # 2. For any function where you want to override the auto comp:
    clang_estimate_function_cost(run_id=RUN_ID, filepath=<file>,
      function_name=<name>)
    # → review the cost_info and decide comp= category manually

    # 3. Annotate the file with optional overrides
    clang_annotate_file(
      run_id         = RUN_ID,
      filepath       = <file>,
      is_entry       = <True if file contains main()>,
      language       = "c",        # or "cpp"
      init_args      = "NULL, NULL, NULL",
      comp_overrides = '{"fn_name": "comm", "other_fn": "io"}'
    )
    # clang_annotate_file writes the file in one in-memory pass;
    # call clang_write_annotated_file only if the tool says it is
    # needed to flush (check the response).

Do NOT manually read files, insert macros with Edit/Write, or run
shell `gcc -fsyntax-only` commands. All of that is handled by the
MCP tools.


══════════════════════════════════════════════════════════════════════
STEP 4 — PER-FILE VALIDATION  (clang MCP tools)
══════════════════════════════════════════════════════════════════════

After annotation (project-level or per-file), validate every annotated
C/C++ file using the two MCP validation tools. Run both for each file:

    clang_syntax_check(run_id=RUN_ID, filepath=<file>)
    clang_lint_annotations(run_id=RUN_ID, filepath=<file>)

clang_syntax_check rules:
  • Uses the real gcc/g++ front-end with a dftracer stub header and
    the session's MPI + dftracer include paths — no manual -I flags needed.
  • PASS → move to next file.
  • FAIL → fix ONLY the exact lines named in the compiler error output:
      - Extract function name and line number from "error:" lines.
      - Call clang_annotate_file again for ONLY that function using
        comp_overrides (e.g. to skip it or override the comp= value).
      - NEVER rewrite the whole file manually. NEVER touch functions
        that already pass syntax check.
      - Retry clang_syntax_check. Max 2 targeted fixes per file.
      - On 2nd failure: strip the single failing function's macros by
        re-calling clang_annotate_file with that function excluded, then
        mark it as PENDING in a comment. Move on.

clang_lint_annotations rules check:
  L1 — DFTRACER_C_INIT before DFTRACER_C_FUNCTION_START in main()
  L2 — comp= UPDATE_STR within 3 lines after every START
  L3 — DFTRACER_C_FINI before MPI_Finalize in main()
  L4 — no END immediately before MPI_CHECK / NCMPI_CHECK
  L5 — no END at global scope

  LINT violations → use clang_insert_line to fix ONLY the reported line;
  never re-annotate the whole file. Then re-lint to verify.

Print per-file status: ✓ <file>  (<n> functions annotated, lint PASSED)


══════════════════════════════════════════════════════════════════════
STEP 5 — BUILD ANNOTATED VERSION  (MCP tools)
══════════════════════════════════════════════════════════════════════

5a. Set DFTRACER_INIT mode:

    Primary (always try first):
      DFTRACER_INIT_ENV = {"DFTRACER_INIT": "FUNCTION"}

    FUNCTION mode works for both C/C++ and Python:
    - C/C++: DFTRACER_C_INIT / DFTRACER_C_FINI macros in source
    - Python: dftracer.initialize_log() / _dft_log.finalize() + decorators

    Fallback (only if FUNCTION produces an empty trace or crashes):
      dftracer_lib=$(python -c "import dftracer; import os; print(os.path.join(os.path.dirname(dftracer.__file__), 'lib', 'libdftracer_preload.so'))")
      DFTRACER_INIT_ENV = {"DFTRACER_INIT": "HYBRID",
                           "LD_PRELOAD": "<dftracer_lib>"}

    PRELOAD-only mode is never used — annotations must always be present.

    Important: NEVER set DFTRACER_INIT=0 — it disables POSIX-level tracing.
    All values are CASE-SENSITIVE uppercase strings.

5b. Build and install annotated version:

    session_build_annotated(run_id=RUN_ID,
      extra_cmake_flags=<same flags as 2b>)

    On failure:
      1. Extract failing function(s) from the compiler error.
      2. Re-annotate only those files using clang_annotate_file with
         the failing function excluded (add it to comp_overrides with
         a sentinel, or use exclude).
      3. Re-run syntax check + lint for the fixed file.
      4. Retry session_build_annotated. Max 2 retries.
      5. If still failing → escalate to user with exact error lines.

5c. Run smoke test:

    session_run_smoke_test(run_id=RUN_ID, command=SMOKE_CMD,
      subfolder="build_ann")

    MPI/OpenMPI as root? Add env:
      OMPI_ALLOW_RUN_AS_ROOT=1, OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1

    Flux proxy systems (Tuolumne, etc.): if SMOKE_CMD contains ``flux proxy``,
    the MCP tool automatically wraps the payload in a script under
    ``<ws>/tmp/run_smoke_test.sh`` that sources lmod init and then runs the
    command.  Never pass inline ``bash -c "module load ..."`` to flux proxy —
    it fails to propagate module state into subprocesses.

    On failure: if DFTRACER symbols in error → re-annotate + retry.
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
  ├─────────────────────────────────────────────────────────┤
  │  STEP TIMINGS (phase 1)                                 │
  │  Step                         Duration                  │
  │  step_1_clone                 N.NNNs                    │
  │  step_2_detect                N.NNNs                    │
  │  step_3_configure             N.NNNs                    │
  │  step_4_build_install         N.NNNs                    │
  │  step_5_smoke_test            N.NNNs                    │
  │  step_6_copy_annotated        N.NNNs                    │
  │  step_7_patch_build           N.NNNs                    │
  │  step_8_annotate              N.NNNs                    │
  │  Timing file: workspaces/<RUN_ID>/step_timings.json     │
  └─────────────────────────────────────────────────────────┘

  Read timing data from the ``step_timings`` field in the tool result
  (or from ``workspaces/<RUN_ID>/step_timings.json`` after the pipeline
  completes) and append each entry to STEP_TIMINGS.

Ask: "Proceed with dftracer trace run? [yes / no / fix <file> <feedback>]"

  "no"  → stop, print artifact location.
  "fix <file> <feedback>" → re-annotate that file using clang_annotate_file
           with comp_overrides derived from the feedback, re-run lint +
           syntax check, rebuild, re-run smoke test, show updated report,
           ask again.
  "yes" → continue to Step 7.


══════════════════════════════════════════════════════════════════════
STEP 7 — TRACE COLLECTION + ANALYSIS  (MCP tools)
══════════════════════════════════════════════════════════════════════

7a. Create the trace output directory before running:

    On LLNL systems (Tuolumne, Lassen, etc.) all trace output MUST go to
    Lustre, not NFS.  ``session_run_with_dftracer`` auto-routes to Lustre
    when ``/p/lustre5/$USER/workspaces/`` exists.  Verify before running:

        mkdir -p /p/lustre5/$USER/workspaces/<app>/{traces,fractals,datasets,runs}

    ``session_run_with_dftracer`` writes ``DFTRACER_LOG_FILE`` to the Lustre
    path and creates a symlink at ``<WS>/traces/`` for downstream tools.
    If Lustre is unavailable (containers, non-LLNL), traces land in ``<WS>/traces/``.

7b. Run with dftracer:

    session_run_with_dftracer(run_id=RUN_ID, command=SMOKE_CMD,
      subfolder="build_ann",
      env_extra={**DFTRACER_INIT_ENV,
                 "DFTRACER_ENABLE": "1",
                 "DFTRACER_INC_METADATA": "1"},
      data_dir="all")

    Always use data_dir="all" so no I/O path is excluded from tracing.
    Always set DFTRACER_INC_METADATA=1 so UPDATE_STR/UPDATE_INT metadata
    (comp=, filename=, count=) is captured in the trace events.
    Always set DFTRACER_ENABLE=1 to ensure trace files are written.

    AFTER the run — check trace file sizes BEFORE splitting:

        ls -lh <WS>/traces/

    EMPTY TRACES DIAGNOSIS (0-byte .pfw.gz files despite DFTRACER_ENABLE=1):

    If dftracer printed "DFTracerCore::initialize" but trace files are 0 bytes,
    dftracer initialized but never finalized.  Diagnose by layer:

    Layer 1 — Main function annotations not appearing:
      The app crashed or exited before reaching finalize().  Common causes:
      - finalize() is called after MPI_Finalize / mpi4py atexit (mpi4py registers
        an atexit that calls MPI_Finalize; dftracer's finalize then tries MPI
        rank queries and crashes).
        FIX: wrap the benchmark call in try/finally and call finalize() inside
        the finally block, before any MPI teardown.
      - An unhandled exception in the benchmark exits the process before finalize().
        FIX: same try/finally wrapping.
      - PyTorch destroy_process_group() or torchrun-hpc teardown runs before
        finalize() is reached.
        FIX: call finalize() immediately after benchmark.main() returns, still
        inside the try block, before any dist.destroy_process_group() in callers.

    Layer 2 — Main annotations appear but DataLoader I/O is absent:
      PyTorch DataLoader workers are forked subprocesses.  In FUNCTION mode,
      dftracer initializes in each worker on fork and finalizes when the worker
      exits cleanly — so worker I/O WILL appear under normal conditions.
      If worker I/O is missing, the workers exited uncleanly (SIGKILL, OOM,
      uncaught exception).  Look for signal or OOM messages in stderr.
      FIX: reduce num_workers, increase memory limits, or check for exceptions
      in worker processes.  Do NOT switch to HYBRID mode just for this — FUNCTION
      mode is correct and sufficient when workers exit cleanly.

7c. Copy trace files up to the parent traces/ dir if needed
    (run_id subdirectory layout):

    cp <WS>/traces/<app_name>/*.pfw.gz <WS>/traces/ 2>/dev/null || true

7d. Split and analyze:

    APP_NAME = first component of RUN_ID
    session_split_traces(run_id=RUN_ID, app_name=APP_NAME)
    session_analyze_traces(run_id=RUN_ID, query_type="summary")


══════════════════════════════════════════════════════════════════════
STEP 8 — OPTIMIZATION PIPELINE  (MCP tools)
══════════════════════════════════════════════════════════════════════

Ask: "Generate optimization proposals? [yes / no]"

If yes:

8a-0. Before each optimization iteration (baseline, opt1, opt2, ...), validate
      structure and repair if needed — every iteration must land in its own
      opt<n>/ run directory, never a hand-built path:

    session_validate_structure(run_id=RUN_ID)
    → if clean=false: session_reorganize_structure(run_id=RUN_ID, dry_run=False)

8a. Run the baseline iteration loop (profiling + diagnosis + literature search):

    session_optimization_iteration(run_id=RUN_ID, command=SMOKE_CMD,
      app_name=APP_NAME, data_dir="all",
      env_extra=DFTRACER_INIT_ENV,
      optimization_applied="baseline", rebuild=False)

    This runs the benchmark, splits traces, diagnoses bottlenecks AND
    searches arXiv/Semantic Scholar for papers relevant to each bottleneck.
    Repeat for each optimization iteration.

    COMPONENT ORDER: bottlenecks are always addressed in the order
    I/O -> communication -> memory -> compute. Severity only breaks ties
    within a component — a critical compute bottleneck is never optimized
    ahead of a medium-severity I/O bottleneck, since I/O fixes are cheaper
    and higher-leverage for most HPC/DL workloads. This is independent of
    the L1/L2/L3 application/middleware/filesystem layering below.

    DEEP-LEARNING WORKLOADS: two additional dimensions are always evaluated
    every iteration, regardless of severity ranking:
      1. Application dataloader / epoch-time performance (fetch_pressure,
         epoch_straggler) — cited against Mohan et al. (VLDB 2021).
      2. Filesystem bandwidth/utilization for the storage the run is on
         (fs_bw) — cited against Lockwood et al. (SC 2018).

8b. Generate citation-backed proposals from the iteration results:

    session_generate_optimization_proposals(run_id=RUN_ID, iteration=-1)

    This maps each diagnosed bottleneck to concrete L1/L2/L3 proposals
    backed by papers found in 8a.

    CRITICAL: Every optimization proposal MUST be backed by a paper citation.
    Papers are not rejected by age but are scored and ranked — the
    highest-ranked paper for each bottleneck becomes the citation.

8b-i. PAPER RELEVANCE SCORING

    For every candidate paper, compute a composite relevance score (0–100):

      score = bottleneck_score + system_score + recency_score

    Component definitions:

      bottleneck_score (0–50):
        How directly does the paper address the specific bottleneck metric?
        First classify the bottleneck's component (I/O, communication,
        memory, or compute) and require the paper to match that component:
        • 50 — paper directly studies this metric within its component
                (e.g., "POSIX close latency in HDF5 timestep writes" for I/O;
                "all-reduce overlap with backward pass" for communication;
                "STREAM memory bandwidth of a kernel" for memory;
                "roofline arithmetic intensity" for compute)
        • 35 — paper addresses the broader component category
                (e.g., "collective metadata performance in parallel I/O",
                "MPI collective algorithm selection", "NUMA-aware memory
                placement", "SIMD vectorization of compute kernels")
        • 20 — paper addresses an adjacent technique that indirectly applies
                (e.g., "MPI-IO collective buffering" for a metadata bottleneck)
        •  0 — paper is topically unrelated to the bottleneck or its component

      system_score (0–30):
        How well does the paper match the target system/software stack?
        • 30 — same library/runtime (e.g., paper uses HDF5 + ROMIO on MPI)
        • 20 — same storage tier or middleware class (e.g., parallel filesystem,
                object store, two-phase I/O)
        • 10 — same application domain (e.g., scientific HPC I/O, checkpoint)
        •  0 — different system class (e.g., cloud-only, database, in-memory)

      recency_score (0–20):
        age_years = current_year - publication_year
        • 20 — age_years ≤ 2
        • 15 — age_years ≤ 5
        • 10 — age_years ≤ 10
        •  5 — age_years ≤ 15
        •  0 — age_years > 15

    Rank all candidate papers by score descending.
    Use the top-ranked paper (highest score) as the primary citation for each
    bottleneck. If the top paper scores < 20 total, add a second search pass
    before accepting it.

8b-ii. PAPER SEARCH PROCEDURE

    If session_generate_optimization_proposals returns empty or unsupported
    bottlenecks, search manually:

      dftracer__search_arxiv / dftracer__search_semantic_scholar with queries:
        - Bottleneck metric name + I/O domain
          (e.g., "posix close latency HDF5 parallel I/O")
        - Broader technique synonyms
          (e.g., "collective buffering MPI-IO", "metadata caching HDF5")
        - System-specific terms from the stack
          (e.g., "ROMIO two-phase I/O", "Lustre striping checkpoint")

      Score every result using the rubric above.
      If top score < 20 after 3 searches → state:
        "Best available citation scores <N>/100 for this bottleneck
         (title: <title>, year: <year>). Proceeding with caveat."
      NEVER propose an optimization with zero candidate papers.

8c. Present proposals in this format (one per bottleneck, top-ranked citation):

    ┌─────────────────────────────────────────────────────────────┐
    │  OPTIMIZATION PROPOSAL — <bottleneck> (<severity>)          │
    ├─────────────────────────────────────────────────────────────┤
    │  Evidence: <paper title>, <authors>, <year>                 │
    │  URL: <arxiv or doi url>                                    │
    │  Relevance score: <N>/100  (bottleneck=N, system=N, age=N) │
    ├─────────────────────────────────────────────────────────────┤
    │  L1 (application):  <specific code/config change>           │
    │  L2 (middleware):   <library/runtime tuning>                │
    │  L3 (filesystem):   <storage/system config change>          │
    └─────────────────────────────────────────────────────────────┘

8d. ITERATIVE OPTIMIZATION LOOP  (max 10 iterations)

    Run up to 10 full optimization cycles. Each cycle applies ALL THREE
    layers (L1 → L2 → L3) then re-profiles to discover new bottlenecks.
    Stop early only if the termination conditions in 8f are met.

    Maintain a loop state table across iterations:

      ITER  | applied_opts          | new_bottlenecks | resolved | raw_rate
      ------|-----------------------|-----------------|----------|----------
      0     | baseline              | <list>          | —        | <val>
      1     | <L1+L2+L3 changes>    | <list>          | <list>   | <val>
      ...

    For each iteration i = 1 … 10:

    8d-i.  Apply all three layers in sequence:

        session_optimize_l1_app(run_id=RUN_ID)
        session_optimize_l2_software(run_id=RUN_ID)
        session_optimize_l3_filesystem(run_id=RUN_ID)

        If any layer reports "no applicable optimizations", record that
        layer as exhausted for this iteration but still run the remaining
        layers. Do NOT skip the re-profile step.

    8d-ii. Re-profile with the applied changes:

        session_optimization_iteration(run_id=RUN_ID, command=SMOKE_CMD,
          app_name=APP_NAME, data_dir="all",
          env_extra=DFTRACER_INIT_ENV,
          optimization_applied="iter-<i>: L1+L2+L3",
          rebuild=True)

        This rebuilds the annotated binary, runs the benchmark, splits
        traces, diagnoses bottlenecks, and searches for new papers.
        Store the trace path for this iteration as TRACE_ITER_<i>.

    8d-iii. Compare this iteration against the previous one using the
        dftracer comparator MCP tool:

        comparator(
          trace_a = TRACE_ITER_<i-1>,   # previous iteration's trace path
          trace_b = TRACE_ITER_<i>       # current iteration's trace path
        )

        The comparator output includes per-metric deltas (improved /
        regressed / unchanged) and a ranked list of changes. Use this
        output — not manual arithmetic — as the authoritative source for:
          • Which bottlenecks were resolved between iterations
          • Which metrics worsened (regression check for 8e)
          • The raw rate and observed completion time delta to show in
            the loop state table

        For the baseline comparison (i = 1), use TRACE_ITER_0 as trace_a.

    8d-iv. Generate updated proposals for newly surfaced bottlenecks:

        session_generate_optimization_proposals(run_id=RUN_ID, iteration=i)

        Score and rank candidate papers per 8b-i. Present any new
        proposals using the format in 8c. Skip bottlenecks that were
        already fully addressed in a prior iteration (no new proposals
        means no proposal box for that bottleneck).

        NOTE: session_optimization_iteration already reads Tier-2 project
        memory (session_memory_retrieve) before issuing a live arXiv query
        for each bottleneck, and auto-reflects (session_memory_write via
        session_memory_reflect) on the previous iteration's outcome once
        this iteration's delta is known — see 'memory_reflection' in its
        return value. Use session_memory_stats() to inspect what the loop
        has learned across all sessions on this system.

    8d-v. Update the loop state table and print a one-line delta summary
        derived from the comparator output:

        "Iter <i>: resolved=<n> bottlenecks, new=<m>, raw_rate <before>→<after> GB/s (comparator: <overall verdict>)"

8e. Check termination conditions after each iteration:

    Stop the loop early (before reaching 10 iterations) if ANY of:

      EXHAUSTED  — All three layers report "no applicable optimizations"
                   for every active bottleneck in this iteration.
      CONVERGED  — No new bottlenecks surfaced AND no resolved bottlenecks
                   changed severity compared to the previous iteration.
      REGRESSED  — The comparator reports that raw write rate or observed
                   completion time worsened by > 5% versus the previous
                   iteration. Use the comparator's metric delta output as
                   the authoritative source — do not recompute manually.
                   (Roll back the last iteration's changes, mark as
                   "regressed", and stop.)
      MAX_ITERS  — 10 iterations completed.

    On stopping, print the reason:
      "Optimization loop ended after <i> iterations: <reason>"

8f. Final all-layers summary after the loop:

    Print the completed loop state table (all iterations).
    Identify the iteration with the best raw write rate as the
    "recommended configuration" and note which optimizations it includes.


══════════════════════════════════════════════════════════════════════
STEP 9 — UPDATE LESSONS LEARNED
══════════════════════════════════════════════════════════════════════

Append any new pitfalls discovered this session to the lessons file.
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

After appending entries, sync them back to the source repo:

    session_lessons_sync_preview(run_id=RUN_ID)   # review the diff

If the preview shows genuinely new entries, ask for confirmation, then:

    session_lessons_sync_pr(run_id=RUN_ID, confirm=True)

This opens a pull request against llnl/dftracer-agents so future sessions
(and other users) inherit the lesson. Requires GITHUB_TOKEN/GH_TOKEN to be
set — if absent, note this in the session report and skip silently rather
than treating it as a failure.


══════════════════════════════════════════════════════════════════════
STEP 10 — SESSION REPORT
══════════════════════════════════════════════════════════════════════

Generate a Markdown session report and write it to
<WS>/session_report.md using the Write tool.

The report MUST follow this exact structure:

---

### DFTracer Session Report — <RUN_ID>

**Application:** <APP_URL> @ <REF>  
**Date:** <YYYY-MM-DD>  
**Platform:** <OS / arch / HPC system>

---

#### What Ran Correctly (Pipeline Flow)

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

Include the full iteration table:

| Iter | L1 applied | L2 applied | L3 applied | New bottlenecks | Resolved | Raw rate |
|------|-----------|-----------|-----------|-----------------|----------|----------|
| 0    | baseline  | baseline  | baseline  | <list>          | —        | <val>    |
| 1    | <change>  | <change>  | <change>  | <list>          | <list>   | <val>    |
| …    |           |           |           |                 |          |          |

For each iteration's applied optimizations, include a sub-section:

##### Iteration <N> — <L1/L2/L3 summary>
- **L1 (app):** <code or config change>
- **L2 (middleware):** <library/runtime tuning>
- **L3 (filesystem):** <storage/system config>
- **Evidence:** <paper title>, <authors>, <year>, score=<N>/100 — <URL>
- **Result:** Before: <metric=val>, After: <metric=val>, Δ = <pct>%
- **Stop reason (if last iter):** <EXHAUSTED / CONVERGED / REGRESSED / MAX_ITERS>

---

#### Lessons Learned (Pitfalls)

For each pitfall encountered, include a sub-section:

##### Pitfall: <short title>
- **Step:** <which pipeline step this occurred in>
- **Error:** (verbatim excerpt from compiler/linker/runtime output)
  ```
  <error text>
  ```
- **Root cause:** <one paragraph explaining why it happened>
- **Fix:** <exact commands or code changes that resolved it>
- **Lesson added to:** <path to lessons file> as tag `<tag>`

---

#### Summary

| Category | Value |
|---|---|
| Files annotated | <N> |
| Functions annotated | <N> |
| Bottlenecks diagnosed | <N> (high: N, medium: N) |
| Optimization iterations | <N> (max 10, stopped: <reason>) |
| Optimizations applied | <N> (L1=N, L2=N, L3=N) |
| Raw write rate improvement | <pct>% |
| Observed completion improvement | <pct>% |
| Papers cited (avg relevance score) | <N> (avg <X>/100) |
| Pitfalls resolved | <N> |

---

#### Step Timings

| Step | Started at | Ended at | Duration (s) |
| ---- | ---------- | -------- | ------------ |
| step_1_clone | ... | ... | ... |
| step_2_detect | ... | ... | ... |
| step_3_configure | ... | ... | ... |
| step_4_build_install | ... | ... | ... |
| step_5_smoke_test | ... | ... | ... |
| step_6_copy_annotated | ... | ... | ... |
| step_7_patch_build | ... | ... | ... |
| step_8_annotate | ... | ... | ... |
| step_8_5_install_dftracer | ... | ... | ... |
| step_9_10_build_and_run | ... | ... | ... |
| step_11_split_traces | ... | ... | ... |
| step_12_analyze_traces | ... | ... | ... |
| step_13_diagnose | ... | ... | ... |

Fill this table from ``STEP_TIMINGS`` (collected throughout the pipeline)
or read ``workspaces/<RUN_ID>/step_timings.json`` which is written at the
end of ``session_run_pipeline``.  The JSON file is the authoritative source
for post-session analysis.

---

#### Artifacts

| Artifact | Path |
|---|---|
| Annotated source | workspaces/RUN_ID/annotated/ |
| Annotated build | workspaces/RUN_ID/build_ann/ |
| Trace files | workspaces/RUN_ID/traces/ |
| Split traces | workspaces/RUN_ID/traces_split/ |
| Step timings | workspaces/RUN_ID/step_timings.json |
| Session report | workspaces/RUN_ID/session_report.md |

---

After writing session_report.md, print its path and a one-line summary
of the key result (e.g., "Raw write rate improved X% via collective I/O
and ROMIO collective buffering — see <path> for full report").


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

NEVER use:
  • Read / Write / Edit tools to manually insert macros into source files
  • Bash `gcc -fsyntax-only` — use clang_syntax_check instead
  • session_annotate_c_file / session_annotate_cpp_file — superseded by
    clang_annotate_file and clang_annotate_project


══════════════════════════════════════════════════════════════════════
ANNOTATION RULES QUICK REFERENCE
══════════════════════════════════════════════════════════════════════

These are enforced automatically by clang_annotate_file/project but
apply when you use comp_overrides:

  comp= "io"   — POSIX I/O, HDF5, NetCDF, MPI-IO file ops, lifecycle fns
  comp= "comm" — MPI wrappers, network, S3, HDFS, RADOS, DFS
  comp= "mem"  — memcpy, malloc/free of large buffers, mmap setup
  comp= "cpu"  — checksums, compression, encryption, hashing

  ALWAYS annotate (never apply score filter):
    • Lifecycle: *_init, *_final, *_initialize, *_finalize
    • Sync/flush: *_fsync, *_flush, *_sync
    • File ops:   *_delete, *_rename, *_stat, *_mknod, *_getfilesize
    • Vendor FS:  gpfs_*, beegfs_*, lustre_*, hdfs_*, ceph_*, daos_*

  Score ≥ 20 (from clang_estimate_function_cost) → annotate
  Score < 20 and not lifecycle/vendor → skip

  INIT/FINI placement in main() (enforced by is_entry=True):
    DFTRACER_C_INIT(NULL, NULL, NULL)  ← before FUNCTION_START
    DFTRACER_C_FUNCTION_START()
    DFTRACER_C_FUNCTION_UPDATE_STR("comp", "cpu")
    ... benchmark work ...
    DFTRACER_C_FUNCTION_END()
    DFTRACER_C_FINI()                 ← after all benchmark work
    MPI_Finalize();                   ← FINI must precede MPI_Finalize

#### Permissions

This skill uses:

- **MCP (session + clang annotation):** `session_create`, `session_configure`, `session_install_dftracer`, `session_build_install`, `session_annotate_c_file`, `session_annotate_cpp_file`, `session_annotation_report`, `session_build_annotated`, `session_copy_annotated`, `session_run_smoke_test`, `session_run_with_dftracer`, `session_run_pipeline`, `session_analyze_traces`, `session_split_traces`, `session_generate_optimization_proposals`, `session_optimization_iteration`, `session_search_optimization_papers`, `session_memory_*`; `clang_annotate_project`, `clang_annotate_file`, `clang_extract_functions`, `clang_estimate_function_cost`, `clang_insert_line`, `clang_syntax_check`, `clang_lint_annotations`, `clang_write_annotated_file`; `analyze`
- **Bash (in `workspaces/<session>/...` only):** `git` (clone), `cmake`, `make`, `module`, `pip`, `flux`, `torchrun-hpc`
- **Write / Edit:** `workspaces/<session>/*` only (source, annotated copies, traces, cores → Lustre when debugging)

Read the annotation lessons file first. Never `sudo`; never build or run from the project root; never write outside the project root.

---

#### Capture a run record after EVERY run (annotated, baseline, opt<n>)

Optimization iterations overwrite the build config, the parameter file, and the
run wrapper **in place**. By the time the final report is assembled that history
is gone, so it must be captured as each run finishes — not reconstructed later.

At the end of every run-producing step:

```
session_capture_run_record(
    run_id=<run_id>,
    run_name="<annotated|baseline|opt1|...>",
    prev_run_name="<previous run>",         # produces the iteration delta
    source_path="<WS>/annotated/source",
    run_script="<WS>/tmp/<run>_run.sh",
    run_log="<WS>/artifacts/<run>_run.log",
    param_files="flash.par",                 # the app's parameter file(s)
    notes="what this iteration changed and why",
)
```

It snapshots, under `<WS>/<run_name>/record/`:

- `build_config/` — `object/setup_call`, `object/Units`, `object/Makefile.h`.
  **On Make-based apps the decisive optimization lives here and a source diff
  cannot see it** (e.g. Flash-X flipping `IO/IOMain/hdf5/serial/PM` →
  `.../parallel/PM` via `+parallelIO`).
- `params/` — the parameter file(s). Apps that read a fixed filename from cwd
  (Flash-X's `flash.par`) lose every earlier config unless snapshotted.
- `../scripts/run.sh` — the exact wrapper used.
- `meta.json` + `../patches/from_<prev>.record.diff` — the iteration's delta.

Also call `session_snapshot_run_source` when the run has its own source tree.

#### Assemble the deliverable at the end

`session_final_report(run_id, report_md, conversation_md, readme_md)` builds
`<WS>/final_report/` containing `patches/`, `scripts/` (`install.sh`,
`run_<case>.sh`, `run_all.sh`), `params/<case>/`, `plan/`, `logs/`, plus
`REPORT.md`, `CONVERSATION.md`, and `README.md`. The three narrative documents
are supplied by the agent — the tool never invents findings.

Backfilling records after the fact does **not** work: intermediate build configs
and parameter files have already been overwritten. Capture as you go.

#### All logs go to `artifacts/`

Build output, run stdout/stderr, saved Bash output, scratch diagnostics — all of
it lives under `<WS>/artifacts/`, named `<step>_<what>.log`. `tmp/` is only for
wrapper scripts and scratch inputs. See [[dftracer-references]].


---

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

#### Provenance mode

When the goal is provenance/lineage rather than performance, follow
[[dftracer-provenance]]: plain run → `dftracer-provenance-discover` → user agrees
on artifact + entities (spec) → `dftracer-provenance-annotate` → build + traced run
(`DFTRACER_INC_METADATA=1`, no PAPI/power/GPU/node counters) →
`dftracer-provenance-graph` health gate → report + privacy guard.

## Reference: DataLoader worker crash debugging (dftracer-pipeline/dataloader_crash_debugging.md)

### DataLoader Worker Crash Debugging (dftracer-pipeline)

Root causes for the two known PyTorch DataLoader worker segfault patterns
hit during dftracer annotation, plus the 4-step gdb/core-dump debugging
procedure. Load only when a DataLoader worker crash actually occurs.

  DataLoader worker segfault on cleanup: annotating `__len__` on Dataset
  subclasses causes dftracer's C-level event buffer to overflow (DataLoader
  calls __len__ O(10000+) times per epoch to build sampler state). The overflow
  corrupts memory and causes SIGSEGV in worker processes during teardown after
  training is otherwise complete. The fix is to NEVER annotate __len__ — simply
  omit @_dlp.log from it. The function still works normally; its callers capture
  the timing context. Do NOT attempt to fix this by disabling dftracer in workers.

  DataLoader worker segfault during dftracer finalize (flockfile/BufferManager):
  Root cause confirmed on Tuolumne with DFTRACER_ENABLE_HIP_TRACING=ON.
  When dftracer initializes with HIP tracing in the parent process and PyTorch
  forks DataLoader workers, the forked children inherit open FILE* handles.
  On worker exit, dftracer's C++ destructor calls DFTracerCore::finalize() →
  BufferManager::finalize() → flockfile_wrapper() on the inherited FILE*.
  The file's internal mutex is in an inconsistent state (locked in parent thread
  that no longer exists in child), causing a C++ exception during _Unwind_Resume
  which itself SIGSEGV's. Fix: dftracer bugfix/reinitializelogfile branch adds
  fork detection via getpid()/getppid() so finalize() safely no-ops in forked
  children that haven't re-initialized. To rebuild dftracer from that branch,
  see the manual cmake procedure below.

  HOW TO DEBUG DataLoader worker crashes:
  1. Enable core dumps on Lustre (NOT NFS — NFS truncates to 16K):
       ulimit -c unlimited
       COREDIR=/p/lustre5/$USER/workspaces/<session>/cores
       mkdir -p ${COREDIR} && cd ${COREDIR}
     Then launch Python from that directory so workers write cores there.
  2. Add libSegFault.so for C-level backtrace (survives Python teardown):
       export LD_PRELOAD=/usr/lib64/libSegFault.so:${LD_PRELOAD}
       export SEGFAULT_SIGNALS=all
     NOTE: Python faulthandler CANNOT catch crashes that happen after
     Py_Finalize() (C++ destructors run post-Python). Use libSegFault instead.
  3. After crash, run gdb on the full core (redirect stderr to suppress warnings):
       CORE=/p/lustre5/$USER/workspaces/<session>/cores/<node>-<proc>-<pid>.core
       PYTHON=<ws>/install/bin/python3.13
       gdb -batch -ex "bt 20" -ex "info threads" $PYTHON $CORE 2>/dev/null
     Key: look for frames from libdftracer_core.so to identify dftracer source.
  4. Check whether crash is triggered by a specific env var (bisect):
     Run once with DFTRACER_ENABLE_HIP_TRACING=ON and once without. If the
     no-HIP run completes cleanly, the crash is in librocprofiler-sdk state
     inherited across fork.

  For any of these, simply skip the decorator; the function will still be
  called normally and its callers (which ARE annotated) will capture the timing.

## Reference: dftracer-planning (carried over verbatim)

#### Planning and Progress

For any task that involves more than two steps or uses multiple MCP tools in sequence:

1. **Start with a plan.** Before taking action, write out the numbered steps you intend to follow.
   Example:
   ```
   Plan:
   1. Clone the repository into a new session workspace
   2. Detect the build system
   3. Configure and build (original)
   4. Run smoke test to confirm baseline
   5. Copy source and annotate with dftracer
   6. Build the annotated version
   7. Run smoke test with dftracer (traces collected)
   8. Split and analyze traces
   ```

2. **Report progress after each step.** After completing a step, state:
   - ✅ What was just done and its outcome
   - ⏳ What comes next

   Example after step 3:
   ```
   ✅ Step 3 done — cmake configured and built successfully (install_dir: /workspaces/.../install)
   ⏳ Next: Step 4 — run smoke test against the installed binary
   ```

3. **Surface blockers immediately.** If a step fails, report the error and the remaining steps before asking how to proceed.

#### Live Progress for Expensive Steps

For any step expected to take more than ~10 seconds, announce it **before** calling
the tool, then show key output lines **after** it completes.

**Steps that require a before-and-after report:**

| Step | Expected duration | What to report after |
|------|-------------------|----------------------|
| `session_create` (git clone) | 10–60 s | repo size, ref cloned |
| `session_configure` (cmake/autotools) | 10–60 s | build tool, any configure warnings |
| `session_build_install` (make) | 30 s – 10 min | exit code, last 5 lines of stdout |
| `session_run_smoke_test` | 5–120 s | exit code, full stdout if short |
| `session_install_dftracer` / `session_install_dftracer_utils` | 1–10 min | pip install exit code, features_enabled list |
| `session_build_annotated` (each attempt) | 30 s – 10 min | attempt number, exit code, first compiler error if any |
| `session_run_with_dftracer` (each attempt) | 5–120 s | attempt number, exit code, trace files created |
| `session_split_traces` | 5–60 s | output directory, file count |
| `session_analyze_traces` | 10–120 s | summary output |

**Format for the before-announce:**
```
⏳ Starting <step name> — this may take <estimated time>…
```

**Format for the after-report:**
```
✅ <step name> succeeded (exit 0)
   stdout: <last 5 non-empty lines, or full output if under 10 lines>

❌ <step name> FAILED (exit <N>)
   stderr: <first compiler error or last 10 lines>
   → artifact log: <workspace>/artifacts/<NN>_<step>.log
```

For build steps, always show the **first** compiler error (the root cause), not the flood
of cascading errors. Extract it with:
```bash
grep -m1 "error:" <workspace>/artifacts/10_session_build_annotated*.log
```

#### Context management for heavy stages

Annotation and optimization stages generate large tool-call histories (build logs,
per-file annotation passes, trace analysis output). Two rules keep the supervisor's
context from filling up with that raw history:

1. **Delegate heavy stages to a sub-agent when the harness supports it.** If launching
   a fresh, isolated-context worker is available, run a whole annotation pass or
   optimization iteration there rather than in the main session — let the worker read
   the deep skill content and raw tool output, and have it report back only a compact
   status summary (files annotated, pass/fail, key metrics). This is the same pattern
   used to keep supervisor context clean when exploring or designing large changes.
2. **Prefer compact status tools over re-reading raw logs.** `session_status` and
   `session_validate_structure` already return small JSON summaries rather than raw
   artifact content — query those instead of re-reading `<workspace>/artifacts/*.log`
   or re-running `skill_load` on a large skill you've already read this session. If a
   tool's JSON response already answers the question, don't re-fetch the source file
   behind it.

## Reference: dftracer-project-router (carried over verbatim)

#### Steps

1. Load the sibling files in this skill directory before deciding anything else.
2. Route each pipeline stage to the narrowest subagent that can finish it.
3. Prefer MCP tools over manual inspection when a tool already exists.
4. Escalate to a larger model only when the stage needs cross-step reasoning.

#### Included Context

- Load `rules.md`
- Load `permissions.md`
- Load `pitfalls.md`
- Load `lessons.md`

#### Last Step

Before stopping, update the sibling lesson files with any new routing issue,
tool failure, or model-selection insight discovered during the session.

#### Rules

- Keep stage agents single-purpose.
- Use Haiku for deterministic tool recipes.
- Use Sonnet when output must be interpreted before acting.
- Use Opus only for synthesis-heavy optimization or diagnosis.
- Never let the router perform a stage job itself.

#### Permissions

- Operate only inside the current session workspace.
- Read canonical paths from `session_get_run_paths`.
- Dispatch stage agents against `baseline/`, `annotated/`, `traces/`, and `opt<n>/` only.
- Do not invent paths outside the session-owned tree.

#### Pitfalls

- Routing with too many tools in one agent wastes context.
- A planner that also builds or traces becomes a bottleneck.
- Model escalation should be driven by reasoning complexity, not by habit.

#### Lessons Learned

- Keep the router thin and deterministic.
- Move stage-specific heuristics into the stage skill, not the router.
- Treat file-type annotation as separate subagents, not one generic annotator.

