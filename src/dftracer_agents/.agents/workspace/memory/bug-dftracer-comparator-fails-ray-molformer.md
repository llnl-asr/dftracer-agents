---
name: bug-dftracer-comparator-fails-ray-molformer
description: mcp__dftracer__comparator failed 3 separate times in one session across different query shapes/trace-dir pairs — confirmed tool-level bug, not a data problem
metadata:
  type: feedback
---

**Symptom:** `mcp__dftracer__comparator` (the `dftracer_comparator` binary wrapper) failed at
the tool level 3 separate times in the ray_molformer session, comparing a 2-node `baseline`
trace directory against the 4-node `baseline_4node` trace directory. Attempts included a
6-clause compound `OR` query (a known DSL gotcha per `dftracer-trace-utils`) and a bare
`group_by_dims="cat"` call with no query filter at all — both failed identically (non-zero
exit from the underlying binary).

**Why this is a tool bug, not a data problem:** both trace directories individually analyzed
cleanly via `event_count` and `analyze()` in the same session (27.3M events / 56 chunks for
the 4-node run, correct process/node counts for both), so the input traces are not corrupted
or malformed — the failure is specific to the comparator's cross-directory diff path.

**Status:** not root-caused this session (the allocation used was running low on remaining
time when this was hit). Flagged for the next session with bandwidth to attach a debugger or
add verbose/stderr capture to the `dftracer_comparator` CLI invocation and step through why a
simple 2-directory group-by-cat comparison fails when each directory analyzes fine alone.

**How to reproduce:** point `comparator(baseline=<2-node-compact-dir>, variant=<4-node-compact-dir>,
group_by_dims="cat", ...)` at the ray_molformer session's `baseline/traces/compact` vs
`baseline_4node/traces/compact` (workspace under `ray_molformer/<session>/`).

See also [[dftracer-trace-utils]] for other confirmed comparator/view gotchas.
