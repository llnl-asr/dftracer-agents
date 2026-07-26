---
name: feedback_analysis_parallel_workers
description: Always run dfanalyzer/analysis with a multi-worker cluster; analysis must finish in a few minutes max
metadata: 
  node_type: memory
  type: feedback
  
  modified: 2026-07-26T03:59:48.615Z
---

Trace analysis (dfanalyzer via `mcp__dftracer__analyze`) must complete within a
few minutes at most. The default single local Dask worker is too slow.

**Why:** The user runs the optimization loop many times; slow analysis compounds.

**CRITICAL correctness caveat (2026-07-06):** with many per-rank .pfw.gz files,
`cluster_n_workers>1` RACES on the shared `.dftindex` RocksDB build and silently
ingests only a PARTIAL subset — non-deterministically (observed 4 procs/122k
events, then 3 procs/63k, on the same 64-file dir). Use **`cluster_n_workers=1`**
for reliable full ingest; on a 64-rank IOR baseline (1.75M events) it still
finishes in ~9s — well within the few-minute budget. Always wipe a stale
`.dftindex` and `checkpoint/` dir before re-analyzing (a partial index caches
wrong counts and can crash with "Failed to open RocksDB ... .dftindex/*.log").

**How to apply:** `cluster_type=local`, `cluster_n_workers=1`. Do NOT pass
`cluster_cores` — invalid ClusterConfig key (valid: n_workers, processes,
memory, memory_limit, type). Correct baseline = 64 procs / 8 nodes / 71,275
POSIX ops / 64 GiB. Related: [[feedback_optimization_pipeline_traces]]

**Additional data point (2026-07-25, 1000genome-workflow session):** on a
26M-event/32-rank trace set on a shared Tuolumne node, `cluster_n_workers=32`
crashed with `DFTUtilsError: Resource temporarily unavailable` (32 workers ×
~192 io-threads over-subscribed the node); `cluster_n_workers=8` completed
successfully. Reinforces staying low — don't scale workers up to "match"
rank count on a shared/login-adjacent node.
