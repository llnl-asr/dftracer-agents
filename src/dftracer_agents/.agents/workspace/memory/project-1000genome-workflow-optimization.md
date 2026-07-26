---
name: project-1000genome-workflow-optimization
description: 1000genome-workflow (pegasus-isi) dftracer pipeline on Tuolumne -- pipeline complete (+29.3% workflow speedup), final_report reproducibility validation partial (allocation expired mid-run), 2 real script bugs found+fixed, privacy-clean
metadata:
  type: project
---

Session `1000genome_workflow/20260725_203603`. Full dftracer pipeline against
pegasus-isi/1000genome-workflow (pure-Python Pegasus/PMC workflow) on
Tuolumne. Pipeline stages (annotate/baseline/diagnose/optimize-4-dim/validate)
are all complete: 4-node/32-rank PMC DAG baseline 316.5s -> opt1 244.7s,
**-29.3% (1.29x) workflow-level speedup**, single-replicate n=1 at DAG scale
but corroborated by a pre-registered DAG-critical-path prediction. Two composed
app-level fixes: `IND_ROW_PRECOMPUTE=1` (hoist redundant per-row VCF parsing,
-93.4% standalone) + `IND_TAR_STREAM=1` (in-memory tar streaming instead of
write-then-reread, -13.2% standalone alone, growing to dominate once compute
fix collapsed the parse cost) -- composed -99.5%/193x at the standalone task
level. See `REPORT.md` for full detail incl. the 4-dimension checklist walk
(48 candidates, communication provably zero-ceiling but PMC's C++ binary is
unannotated -- disclosed coverage gap).

**This entry's update (2026-07-26, reproducibility-validation pass):**
`final_report/` was assembled with all required sections (completeness.ok=true,
readme_check.ok=true, pdf.generated=true, 49965 bytes). Self-contained
validation was run with a live Flux allocation (`<flux-jobid>`, 16 nodes):
- Smoke test (`run_smoke_individuals.sh`): fully isolated, PASSED (16.85s vs
  15.61s expected).
- `run_baseline_4node.sh`: PARTIALLY reproduced -- PMC launched correctly and
  began executing real tasks (individuals_ID0000001 in 13.7s, matching the
  reported per-task profile), but the allocation expired mid-DAG before all
  46 tasks finished. `validated=False` recorded honestly (not claimed as pass).
- `opt1` run: not attempted, allocation ran out.

**Two real script bugs found and fixed in `final_report/scripts/` (not just
flagged):**
1. `run_smoke_individuals.sh` never activated the session's venv
   (`${WS}/tools/venv/bin/activate`), so `python3` resolved to system Python
   without dftracer installed -- `ModuleNotFoundError: dftracer.python`.
2. `run_baseline_4node.sh`/`run_opt1.sh` depended on an external
   `${WS}/baseline_4node/scripts/pmc_wrapper.sh` outside `final_report/` with
   hardcoded absolute paths (violates self-containment, Pipeline Policy rule
   15) -- now inlined directly into the `final_report/scripts/` versions.
   Also: without `pegasus-mpi-cluster -s/--skip-rescue`, PMC recognized the
   DAG's existing `.rescue` state as already-done and returned a false-instant
   success (`tasks=46, submitted=0, succeeded=0` in 0.001s) instead of a real
   reproduction -- fixed by adding `-s -r <OUTPUT_ROOT>/....rescue`.

**Known structural limitation (disclosed in REPORT.md, not fixed):** the PMC
DAG (`baseline_4node/1000-genome-pmc-run-4node/*.dag`) is a Pegasus-planned
artifact with per-task absolute paths baked in at `pegasus-plan` time, so it
cannot be relocated under an isolated `OUTPUT_ROOT` without re-running
`pegasus-plan` from scratch -- `scripts/install.sh` remains a documented TODO
stub for that from-scratch rebuild.

**MCP tool bug discovered this pass (flagged, not fixed at tool level):**
`session_final_report` called without real content in
`report_md`/`readme_md`/`conversation_md` silently overwrites existing
detailed content with a near-empty default template instead of preserving it.
This happened TWICE in this session (once from omitting the args, once from
accidentally passing a placeholder string) and required full manual
reconstruction from `pipeline_plan.md`/`pipeline_plan_step8_results.md`/
`pipeline_plan_changelog.md` both times. **Always pass the full real content
for all three `_md` arguments on every call.** Proposed fix: the tool should
preserve existing file content when a `_md` arg is omitted or empty, or
require an explicit `overwrite=True`.

**Resume point:** obtain a fresh Flux allocation with >=15-20 min budget,
`cd final_report/`, set `WORKSPACE_ROOT` in `config.ini`, run
`bash scripts/run_all.sh <new-alloc-id>`, compare against REPORT.md Section
2/7 row 10, then re-call `session_final_report(validated=True, ...)` with the
full real `_md` content preserved from the current REPORT.md/README.md/
CONVERSATION.md.

Privacy scan: clean (598 files scanned, including `final_report/`).
