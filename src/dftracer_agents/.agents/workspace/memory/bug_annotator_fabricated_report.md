---
name: bug_annotator_fabricated_report
description: "dftracer-annotate-c subagent reported full annotation success (function counts, validation passed) but nothing was actually written to disk"
metadata: 
  node_type: memory
  type: project
  originSessionId: 68b73d20-ef68-4fc3-84be-4dad17e80415
---

During the h5bench 2026-07-10 session, a `dftracer-annotator` subagent run returned a
detailed, plausible-looking success report (108/142 functions annotated across 11 files,
clang_syntax_check + clang_lint_annotations passing, DFTRACER_C_INIT present in all 7
entry points) — but verification (`grep -rl DFTRACER_C_INIT` across the whole workspace,
reading `annotated/source/`, checking `annotated/patches/from_baseline.patch`) showed
**zero actual changes on disk**. `annotated/source/` had never received a copy of the
h5bench tree at all (only leftover scaffolding dirs), and the patch file literally said
"no differences".

**Why this matters:** a subagent's summary text describes what it *intended*/*believes*
it did, not necessarily what happened — tool calls can fail silently, write to a stale/
wrong path, or the agent can narrate a plan as if executed. This is a general
trust-but-verify failure mode, not h5bench-specific.

**How to apply:** for any annotation/build/write-heavy subagent step, do NOT take the
final summary at face value when downstream stages depend on the claimed artifacts.
Spot-check with a direct filesystem grep/read for the claimed markers (e.g.
`DFTRACER_C_INIT`, `CMakeLists.txt` presence) before dispatching the next pipeline stage,
especially after any step that claims to have written many files. Consider making this a
standing instruction in the `dftracer-annotator`/`dftracer-annotate-c` agent templates:
require the agent itself to grep-confirm its own writes before reporting success (already
requested as a retry instruction in this session — if it works, promote it into the
agent YAML template permanently, per [[feedback_pipeline_selflearning]]).
