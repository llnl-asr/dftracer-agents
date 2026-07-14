---
name: feedback-dftracer-service-node-counters
description: Always run dftracer_service (node-level counters) one instance per node, pinned to one core, as part of every run script
metadata:
  type: feedback
---

Every job run (smoke test, best-case trace, N-node validation run) must start the `dftracer_service` background daemon — it captures node-level counters, separate from per-rank application traces — with ONE instance per node, pinned to a single core, bracketing the actual job launch. This is a standing rule, not session-specific: `session_service_start(run_id=...)` before the run, `session_service_stop(run_id=...)` after, every time, on every node in the allocation.

**Why:** User stated this as a hard rule — node-level counters from the service daemon are expected output of every run, not optional instrumentation. The service resolves the `dftracer_service` binary from the session's own `install_ann/bin/` first (i.e. the pip-installed dftracer build for this session), falling back to PATH — so as long as dftracer was pip-installed into the session env (which it always is per this project's install steps), no extra setup is needed to make the binary available.

**How to apply:** In every run-launch wrapper script for `dftracer-tracer` / `dftracer-optimizer-*` steps: call `session_service_start` immediately before `session_run_with_dftracer` / the flux launch, and `session_service_stop` immediately after the job completes — for every rank-launching job, not just the final validation run. Service traces land at `<workspace>/traces/service_<hostname>.*`, separate from app traces at `<workspace>/traces/<run_id>.*`; both get picked up by `session_split_traces`. Pin the daemon to one core per node (leave the rest for the app ranks) — do not let it compete with application ranks for a full core count. See [[feedback-optimization-pipeline-traces]].
