---
name: dftracer_report
description: Final dftracer pipeline stage. Synthesize the full session report from every earlier agent's outputs into a self-contained, reproducible final_report/ (REPORT.md + PDF, README, CONVERSATION, config.ini, scripts, patches, plan, performance), capture run records before iterations overwrite them, verify the package deterministically, then finalize the session (persist approved self-learning, agents_sync, memory_write, privacy scan/redact until clean). Load at the end of every pipeline, or when session_final_report returns completeness.ok false, readme_check.ok false, pdf.generated false, or session_final_report_verify fails.
---
Cross-references: [[dftracer_privacy]], [[dftracer_isolation]], [[dftracer_profiling]], [[dftracer_skills]], [[dftracer_graph]], [[dftracer_provenance_graph]], [[repro_package]]

# dftracer report: final stage

You READ what earlier agents produced and compile it into one coherent document; you do NOT run new tools on the app
or apply new optimizations.

## Rules

### Tool-first (MANDATORY)
Use the MCP tools before any manual reading or custom shell: `session_read_file` (agent reports, plan files),
`session_get_run_paths` (NEVER hand-build paths), `session_diagnose_bottlenecks` / `session_generate_optimization_proposals`
(if not already done), `comparator` (baseline vs optimized), `event_count`, `reader`, `graph_query(mode="docs")` for
the specific rule you need. Tools missing: ask the user to start the dftracer MCP server. Tools erroring: fix the tool or
its wiring before falling back.

### Per-agent contribution collection (MANDATORY)
Every agent writes its artifacts under `<WS>/artifacts/`; collect them:

| Agent | Expected artifacts | What to collect |
|---|---|---|
| session-setup | `session_report.md` | Session ID, system config, source URL |
| build-app | `build_report.md`, `build.log` | Build status, compiler flags, dependencies |
| annotate-* | `annotation_report.md` | Files annotated, coverage %, skipped files |
| build-smoke | `smoke_test_report.md` | Smoke test result, runtime, errors |
| tracer | `trace_collection_report.md` | Trace paths, event counts, trace quality |
| analyzer | `analysis_report.md` | Tool findings vs manual analysis table |
| diagnoser | `diagnosis_report.md` | Bottleneck list, severity scores, metrics |
| optimizer | `optimization_report.md` | Proposals applied, citations, iteration deltas |

If an artifact is missing, say so explicitly in the report. Do not fabricate findings; ask the main thread to re-run
the missing agent.

### Report structure (DEFAULT TO DETAILED, user instruction 2026-07-22)
A 4-6 line prose summary is NOT acceptable: every baseline run and every optimization (applied AND rejected) gets its
own row with real numbers. If a step measured something, the number goes in the report.

**Per-optimization triple, MANDATORY for EVERY optimization touched (applied, rejected, deferred; user instruction
2026-07-25):** (1) code/config snippet: the actual before/after diff, the exact flag/env var/API call; (2) measured
comparison against its baseline, or "not measured" stated explicitly; (3) what's next: one line minimum per optimization
(applied: what to tune further or at what scale to re-validate; rejected: whether new evidence could flip the verdict or
it is a closed case). It rolls up into section 11, but each optimization's subsection carries its own line.

1. **Executive Summary**: session ID, app, system; overall status (success / partial / failed); top 3 bottlenecks; top
   3 optimizations applied; key metric improvement.
2. **Baseline Detail** (do not compress): every baseline/reference run in a table: run name | config (scale, epochs,
   dataset, batch size) | measured metric(s) | what it was compared against. Several baselines: show all and why (a
   re-baseline after a tooling fix is itself a finding).
3. **Per-Agent Contributions**: per agent: name, what it did (one sentence), key findings, artifacts produced, tool
   findings vs manual analysis.
4. **Tool Findings vs Manual Analysis**: table Finding | Source (TOOL or MANUAL) | Details; required for analyzer and
   diagnoser; missing table is noted.
5. **Bottleneck Diagnosis**: ranked list (I/O -> comm -> mem -> compute), severity scores, metric evidence.
6. **Optimization Proposals**: L1 (application), L2 (middleware), L3 (system); each with description, citation
   (authors, title, venue/year, URL), expected impact, status (applied / pending / unsupported).
7. **Optimization Ledger** (core deliverable whenever optimization ran): one table, EVERY dimension walked, EVERY
   proposal considered: `# | Dimension | Level | Change | Applied? | Measured delta | Significance/replicates | Verdict`.
   Applied? = yes / yes-then-reverted / no (not applicable, with reason); measured delta = the number, or "not measured";
   replicates and the comparator's significance flag (~/*/**), single-replicate results flagged directional; verdict =
   kept / reverted / harmless-but-not-credited / needs more replicates. Never omit a rejected/reverted optimization (e.g.
   torch.compile's warmup-confound reversal, Spindle's measured 3-4x slowdown). Below the table, a short mechanism
   paragraph for the 2-3 highest-impact rows.
7b. **N-Way Comparator Table** (whenever more than one config was measured): one row per distinct configuration,
   baseline first: `Config | Level | Scale | Write | Read | delta vs baseline | Replicates | Range/CV | Overlaps baseline? | Verdict`
   (deltas from medians; a delta whose range overlaps the baseline's is not a real effect; verdict kept /
   rejected-no-effect / rejected-regression / rejected-not-range-clean / not-yet-validated; footnote configs that are not
   directly comparable). **Code/config changes as snippets (added 2026-07-24)** for every row and every annotation fix:
   the before/after block, the exact CLI flag or env var, or the exact HDF5/MPI-IO property call
   (`--hdf5.setAlignment=1m` / `H5Pset_alignment`), so every row is reproducible from the report alone.
8. **Iteration Results**: iteration | applied change | metric before | after | delta%; best configuration; convergence
   status (converged / exhausted / regressed / max_iters).
9. **Lessons Learned**: skills created/updated, agent definition changes, MCP tools added/fixed, pitfalls and fixes.
10. **Recommendations for Future Runs**: what worked, what needs improvement, known limitations.
11. **Remaining Work / Resume Point** (MANDATORY, makes the session resumable): outstanding items (not yet validated,
   deferred, blocked, needs replicates) in one place; unmeasured but applicable candidates with the exact next
   command/config; tool/skill gaps hit and NOT fixed, separated from those that were fixed; exact resume instructions (the
   run_id, which step to re-invoke, state to load first: session_status, pipeline_plan.md, the session's memory entry).
   Complete: write "Nothing outstanding; pipeline complete".

### session_final_report gates (AUTOMATIC as of 2026-07-25: verify, don't improvise)
- `pdf`: the tool renders REPORT.md -> REPORT.pdf every call (pandoc if present, else markdown-it-py + xhtml2pdf, no
  sudo) and writes `scripts/render_pdf.sh`. `generated: true` -> note path/size; `false` -> read `reason`, fix the cause
  (network/proxy for pip, `module load` pandoc), re-call. Never ship without a PDF.
- `completeness` `{ok, missing, warnings}`: checks REPORT.md against the structure above (sections, patches/ when opt
  runs exist, code fences per optimization, "what's next" language). `ok: false` -> fix `missing` and re-call. Act on
  warnings unless documented. A genuine recurring false positive is fixed in `_validate_report_completeness`.
- `readme_check` `{ok, missing}`: `scripts/readme_smoke_test.sh` (written and run every call) checks that README names
  every script, explains config.ini/WORKSPACE_ROOT, has a literal runnable command, and points to REPORT.md. As
  blocking as the PDF and completeness.
- README must let someone with ONLY `final_report/` reproduce: `cp -r final_report/` (or OUTPUT_ROOT at a scratch dir),
  the exact config.ini edit, the exact invocation of every script in order, what each does, how to tell it worked
  (pointer to REPORT.md numbers, what "within noise" means).

### Reproducibility package (MANDATORY)
`config.ini` is the ONLY place a real path/session value goes; every script sources `scripts/lib_load_config.sh`;
collected scripts are anonymized automatically. Your job:
1. Verify `scripts/` covers every run of the session end to end, including bespoke wrappers the `tmp/*<run>*.sh` glob
   missed (topology/env A/B tests, `perf stat` roofline checks): add them sourcing the loader.
2. Run the self-contained validation: `session_verify_isolation(run_id, package_dir="final_report", mode="static")`
   until clean, then `mode="launch"` (fresh copy, `env -i bash -l`, OUTPUT_ROOT outside the workspace), poll
   `mode="status"`; fallback: point OUTPUT_ROOT at an isolated dir, fill WORKSPACE_ROOT, run
   `scripts/run_all.sh <alloc-id>` with ONLY what is in `final_report/`, confirm it reproduces within noise.
3. Re-call `session_final_report(validated=True, validation_notes=...)` once it passes; never for a failed run.
4. No script in `final_report/scripts/` may contain an absolute path, username, or job/allocation id.
5. `session_capture_run_record` at the END of every run-producing step (annotated, baseline, each opt<n>): iterations
   overwrite build config, parameter files and wrappers in place.

### End-of-session finalization (MANDATORY, in this order)
1. Persist approved self-learning (YAML templates, never rendered copies; shared prose in common-sections).
2. `agents_sync()`: report its summary; surface conflicts.
3. `memory_write(...)` (type=project; user guidance type=feedback); update the existing memory for this effort
   (`memory_list` first).
4. `privacy_scan()` clean over everything the session added to the git-tracked trees; else `privacy_redact()` and re-scan.
5. `privacy_scan()` / `privacy_redact()` on `final_report/` too, ALWAYS (config.ini is the one file allowed real values).
End the report with a "Finalization" block stating each step's result. Then `dftracer_report_check` must pass.

## Constraints
- Never invent results: REPORT.md, CONVERSATION.md and README.md bodies come from the caller; the tool never re-runs the app.
- Raw run/build logs stay in `artifacts/`, out of the package.
- Never report `validated=True` for a run that did not pass; never imply the git history is clean when only the
  working tree was redacted.
- `session_lessons_sync_pr` and other shared-repo actions only with the user's explicit approval.

## Corner cases
- **KNOWN BUG (confirmed, AF3 session, 2026-08-02)**: `session_final_report` regenerates
  `install.sh`/`run_all.sh`/`config.ini`/`plan/*.md`/`PERFORMANCE.md`/`performance/mlflow.json` from boilerplate on
  EVERY call, discarding hand customization and undoing a prior redaction; its own boilerplate leaked the absolute
  workspace path (install.sh comment) and baked a literal flux jobid into run_all.sh's default argument. Sequence around
  it: hand-customize first, call `session_final_report` for the gates, run the real validation, make the final fixes it
  revealed, then `privacy_redact()`/`privacy_scan()` LAST, and do not call `session_final_report` again after that
  final pass (not even to flip `validated=True`). Fix target: `final_report.py` (idempotent/additive regeneration,
  placeholders in boilerplate); not yet fixed.
- Sessions with zero optimization runs: the patches/code-block checks are gated on opt runs; a mismatch is reported to
  the user, not silently worked around.
- `patches/` empty with opt runs: the app kept no per-run `source/` snapshot and no `annotated/src/<name>_opt<N>` variant
  files, or `session_capture_run_record` was never called for those runs.
- `overwrite=False` (default) preserves existing REPORT/CONVERSATION/README when a body is omitted; `True` forces regeneration.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
`dftracer_report_check` / `session_final_report_verify` report a failed check; repair, then re-verify:
- `structure` -> call `session_final_report`.
- `config` -> config.ini needs `WORKSPACE_ROOT=`.
- `privacy` -> `privacy_redact(paths=[<final_report>])`, then `privacy_scan`; only config.ini may keep real paths;
  mind the regeneration bug above.
- `syntax` -> fix the script `bash -n` names.
- `loader` -> source `scripts/lib_load_config.sh` in every run/install script.
- `runs` -> add `scripts/run_<run>.sh` for each measured run and call it from `run_all.sh`; capture run records.
- `patches` -> `session_capture_run_record` for the annotated run.
- `readme` -> fix README.md as the smoke test's `missing` lines say.
- `report_sections` -> add the missing sections, re-call `session_final_report`.
- `pdf` -> read `pdf.reason`, fix, re-call.
- `final_report_present` (inconclusive) -> call `session_final_report` first.
The same failure twice stops the loop: ask the user.

## Self-heal
- A tool misbehaves: fix `mcp_tools/tools/session/final_report.py` in dftracer-agents (or `capability_update` this
  capability) and ask for an MCP restart; never fall back to manual assembly when a restart is what is needed.

## Self-learn
- What is explored here becomes a capability: a reproducibility trap specific to a workload or system becomes a lesson
  of its `workload_<app>` / `system_<machine>` capability; a report gate that keeps being missed becomes a check here;
  see capability_builder.
- Feed lessons back into skills before you stop, through the confirmation gate; redact before persisting.

## What works
- 2026-07-25: PDF, completeness and README smoke test are produced by `session_final_report` itself; verify their
  return values instead of improvising.
- 2026-08-02 (AF3): the safe order is customize -> `session_final_report` -> validation run -> final fixes ->
  `privacy_redact`/`privacy_scan` last, no further `session_final_report` call.
