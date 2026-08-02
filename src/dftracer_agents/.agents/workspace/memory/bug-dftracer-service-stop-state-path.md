---
name: bug-dftracer-service-stop-state-path
description: RESOLVED 2026-07-20: dftracer_service stop "No running server found" fixed upstream (per-hostname pid files) + required a non-exclusive flux invocation
metadata:
  type: feedback
---

**Canonical home:** see the `software-pecan` skill (the validated non-exclusive
`-n<n> -c1` invocation pattern) — superseded in turn by
`bug-dftracer-service-start-blocks-flux-run` (`flux run` itself must not be used to
START the daemon; `flux submit` detached is required).

**RESOLVED 2026-07-20.** `dftracer_service stop` reliably reporting "No running server found" even when the daemon was confirmed running was a real upstream bug, now fixed — see [[feedback-dftracer-service-node-counters]] for the full validated fix and the corrected `flux run` invocation pattern.

Two compounding causes, both now fixed:
1. **Upstream dftracer bug**: the service's pid state was written to a single shared `dftracer_server.pid` filename across all nodes instead of a per-hostname one, causing collisions. Fixed in dftracer's `3e6fc42` develop commit (`fix(service): fix pid file not found on smoke test`) — pid files are now `dftracer_server_<hostname>.pid`, one per node.
2. **Invocation bug (ours, not dftracer's)**: driving `dftracer_service` via `flux run -N<n> --tasks-per-node 1 ...` silently defaults to exclusive full-node reservation on this flux config, which blocks a separate `stop` job from co-scheduling onto the already-running service's nodes at all — `stop` would hang pending, or (if forced onto different/wrong nodes) genuinely find no server. Fixed by using `flux run -N<n> -n<n> -c1 ...` instead (explicit per-task cores, not `--tasks-per-node`), which does not reserve nodes exclusively.

**Validated**: upgraded to dftracer `3e6fc42`, used the `-n<n> -c1` invocation for both `start` and `stop`, ran a full 16-rank DDP training job co-located with the service on the same 4 nodes — `stop` cleanly sent SIGINT to all 4 nodes' servers with zero errors.

**How to apply:** upgrade to a dftracer build with the pid-file fix (`3e6fc42` or later on `develop`), and always use `-n<n> -c1` (never `--tasks-per-node`) when driving `dftracer_service` via raw `flux run`. The old workaround (falling back to `flux cancel` on the service's job id) is no longer necessary but still works as an emergency fallback if a future regression reappears.
