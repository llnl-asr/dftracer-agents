---
name: feedback-dftracer-service-node-counters
description: Always run dftracer_service (node-level counters) one instance per node, pinned to one core, as part of every run script — use -n<n> -c1, never --tasks-per-node; also required env vars must be set or it silently no-ops
metadata:
  type: feedback
---

Every job run (smoke test, best-case trace, N-node validation run) must start the `dftracer_service` background daemon — it captures node-level counters, separate from per-rank application traces — with ONE instance per node, pinned to a single core, bracketing the actual job launch. This is a standing rule, not session-specific: `session_service_start(run_id=...)` before the run, `session_service_stop(run_id=...)` after, every time, on every node in the allocation.

**Why:** User stated this as a hard rule — node-level counters from the service daemon are expected output of every run, not optional instrumentation. The service resolves the `dftracer_service` binary from the session's own `install_ann/bin/` first (i.e. the pip-installed dftracer build for this session), falling back to PATH — so as long as dftracer was pip-installed into the session env (which it always is per this project's install steps), no extra setup is needed to make the binary available.

**CRITICAL fix (validated 2026-07-20):** when driving `dftracer_service` via a raw `flux run` (e.g. when `session_service_start`/`stop` aren't available or you're debugging manually), NEVER use `--tasks-per-node 1` — on this flux config it silently reserves ALL requested nodes EXCLUSIVELY (`"exclusive": true` in the resolved jobspec, even with no `--exclusive` flag), blocking the training job or a later `stop` job from co-scheduling onto the same nodes. Use explicit `-n<n> -c1` instead:
```bash
flux run -N<n> -n<n> -c1 dftracer_service start <dir>   # 1 task/node, 1 core each — NOT exclusive
flux run -N<n> -n<n> -c1 dftracer_service stop <dir>    # same pattern to stop
```
Verify with `flux job info <id> jobspec` (must NOT show `"exclusive": true`). Also: dftracer's `3e6fc42` develop commit fixed a real pid-file collision bug (`dftracer_server_<hostname>.pid` per node, replacing a single shared `dftracer_server.pid` that caused persistent "No running server found" errors on `stop` — see [[bug-dftracer-service-stop-state-path]], now resolved once combined with the non-exclusive invocation above).

**Pitfall confirmed 2026-07-22: the daemon silently no-ops if required env vars aren't set — it does NOT error.** `dftracer_service start <dir>` produced a 0-byte `.out`/`.err` and NO trace file at all when `DFTRACER_ENABLE`/`DFTRACER_LOG_FILE` were not exported in the same invocation — these are REQUIRED, not optional, despite no error/warning being emitted. Always verify a service run actually produced a non-empty per-node trace file before trusting it for memory-headroom or any other node-counter analysis — an empty/missing trace after `start` means the env vars were missing, not that there was nothing to report.

**How to apply:** In every run-launch wrapper script for `dftracer-tracer` / `dftracer-optimizer-*` steps: call `session_service_start` immediately before `session_run_with_dftracer` / the flux launch, and `session_service_stop` immediately after the job completes — for every rank-launching job, not just the final validation run. Service traces land at `<workspace>/traces/service_<hostname>.*`, separate from app traces at `<workspace>/traces/<run_id>.*`; both get picked up by `session_split_traces`. Pin the daemon to one core per node (leave the rest for the app ranks) — do not let it compete with application ranks for a full core count. If `session_service_start`/`stop` MCP tools are unavailable and you fall back to raw `flux run`, use the `-n<n> -c1` pattern above, never `--tasks-per-node`, AND export `DFTRACER_ENABLE`/`DFTRACER_LOG_FILE` explicitly in that same invocation, then verify the resulting trace file is non-empty. See [[feedback-optimization-pipeline-traces]].
