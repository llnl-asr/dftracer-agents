---
name: bug-dftracer-stats-categories-zero-events
description: dftracer_stats --report categories returned 0 events on compacted traces (2026-08); on raw per-rank traces it works (verified 2026-09-30) — re-check before relying on it for compacted dirs
metadata:
  type: project
---

Original bug (2026-08-02): `dftracer_index --rebuild-summaries` never scanned event content ("Events processed: 0") so `dftracer_stats --report categories/summary` failed on compacted (split) traces; workaround was a direct gzip+json parse.

Update 2026-09-30: on RAW per-rank traces, `dftracer_stats -d <raw_dir> --index-dir <idx> --report categories` and `--report top-names --top-n N` work correctly and fast (358 M events indexed + counted in ~70 s, 585 M in ~100 s, 32-48 threads). Not re-tested on compacted traces.

**Why:** per-category event counts are a routine user question.
**How to apply:** use dftracer_stats on raw trace dirs first; if a compacted dir reports 0 events, fall back to raw dir or direct parse. See [[tools-dftracer-utils]].
