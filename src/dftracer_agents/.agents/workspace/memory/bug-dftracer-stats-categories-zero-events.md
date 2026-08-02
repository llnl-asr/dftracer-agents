---
name: bug-dftracer-stats-categories-zero-events
description: "dftracer_index --rebuild-summaries reports \"Events processed:0\" and never populates per-chunk stats, so dftracer_stats --report categories/summary always fails with \"No chunk statistics in index\" on compacted traces"
metadata: 
  node_type: memory
  type: project
  originSessionId: 0f04330e-c574-4c53-bd33-2ef7a37bbfd7
  modified: 2026-08-02T08:49:21.628Z
---

`dftracer_index --rebuild-summaries` (and the `mcp__dftracer__index` MCP wrapper) only builds a bloom-filter existence index — file-level resolution/caching works fine (`dftracer_stats` reports `N total, N cached, 0 failed`) — but it reports `"Events processed: 0"` and never actually scans decompressed event content to populate per-chunk statistics. Every subsequent `dftracer_stats --report categories` (or `summary`/`detailed`/etc.) call then fails per-file with `"No chunk statistics in index for <file>"`.

**Confirmed NOT a checkpoint-size mismatch**: reproduced identically with default `--checkpoint-size` and with an explicitly small value (1MB) matched to the ~4MB split chunk size. `event_count` is unaffected and reports correct totals via a different code path.

**Why:** this is an upstream bug in the `dftracer-utils` PyPI package's C++ `dftracer_index`/`dftracer_stats` binaries. No local source checkout exists in this project (pip-installed prebuilt only), so it can't be patched here — needs an upstream fix to `--rebuild-summaries` so it actually walks event content.

**How to apply:** don't trust `mcp__dftracer__stats` (report=categories/summary/detailed) on a compacted trace directory — it will silently return all-failed results per file. For a category or per-category-count breakdown, decompress and parse directly (gzip + json line-by-line, `.strip().rstrip(",")`, skip `[`/`]` bracket lines) across ALL chunks in the compact dir, not a sample — category distribution can be very uneven (e.g. a torch-profiler-bridge `PP` category was <0.04% of total events in one run and would've been missed sampling only 3 of 56 chunks). `event_count` remains reliable for a total count. Full writeup + code snippet in the `tools-dftracer-utils` skill.

See [[software-ray-molformer]] for the session where this was hit while verifying full event-category coverage after a training run.
