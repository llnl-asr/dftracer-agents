---
name: bug-dftracer-service-stop-state-path
description: dftracer_service stop reports "No running server found" even though the daemon is confirmed running — unfixed, workaround is flux cancel
metadata:
  type: feedback
---

`dftracer_service stop <state_dir>/$(hostname)` reliably reports "No
running server found" on every node, even when the daemon (started with
the identical `<state_dir>/$(hostname)` path prefix) is confirmed still
running via `flux jobs`. Reproduced twice in one session, on two
independent 8-node runs.

**Why:** the service's actual state file/socket is apparently not written
under the exact path prefix passed to `start` — root cause not yet found
(didn't have time to trace the binary's internals under a shared
allocation). This means `dftracer_service stop` cannot currently be relied
on to cleanly tear down the daemon.

**How to apply:** until fixed, after any run that starts
`dftracer_service`, verify teardown by checking `flux jobs` for the
daemon's job id and `flux cancel` it directly if `stop` reports "No
running server found" — don't assume `stop`'s exit code/message means the
daemon is actually gone. If a session is short on allocation time and hits
this repeatedly, it is acceptable (with user sign-off) to temporarily drop
the `dftracer_service` bracket from a run script rather than lose time to
repeated cleanup — but this is a deviation from the standing "always run
the service" rule, not a permanent fix, and should be reverted once the
state-path bug is actually found and fixed. See [[feedback-dftracer-service-node-counters]].
