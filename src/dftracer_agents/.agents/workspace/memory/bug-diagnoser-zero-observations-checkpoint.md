---
name: bug-diagnoser-zero-observations-checkpoint
description: diagnose() reports 0 metric observations against a checkpoint the analyzer just populated with real data — reproduced twice, workaround is reading analyzer's own console summary
metadata:
  type: feedback
---

`mcp__dftracer__diagnose` against an `analyzer.checkpoint=True` checkpoint
dir returned `"0 metric observations across 0 view(s), 0 high/critical
issue(s) found"` even though the immediately-preceding `analyze()` call
against the same trace printed a normal, populated console summary
(hundreds of millions of POSIX ops, real MB/bandwidth numbers) and left
`_flat_view_*.parquet` files on disk in that same checkpoint dir.
Reproduced on two independent traces in one session (a near-empty/no-I/O
trace and a data-rich I/O-enabled trace) — not a one-off fluke.

**Why:** the diagnoser isn't correctly reading the flat-view parquet the
analyzer just wrote, at least in some configuration/schema combination —
root cause not yet isolated (didn't have time to trace the parquet schema
mismatch under a shared allocation).

**How to apply:** don't block a report or bottleneck ranking on
`diagnose()` returning a non-empty severity table. If it comes back with 0
observations against a checkpoint you know has data (cross-check via the
analyzer's own printed summary or `event_count`), fall back to reading the
analyzer's console output directly (Time Period Summary / Layer Breakdown
tables) and reason about severity from those raw numbers instead. See
[[bug_analyze_timeout_bytes]] and the `dftracer-diagnoser` skill pitfalls
for related analyzer/diagnoser reliability gaps.
