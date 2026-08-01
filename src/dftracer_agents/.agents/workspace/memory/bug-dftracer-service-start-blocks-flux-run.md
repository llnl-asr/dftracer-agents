---
name: bug-dftracer-service-start-blocks-flux-run
description: "dftracer_service start under `flux run` never returns (daemon stays a child of the task) — must use `flux submit` detached; corrects the earlier -n<n> -c1 `flux run` guidance"
metadata: 
  node_type: memory
  type: feedback
  
  modified: 2026-07-29T06:42:41.794Z
---

`flux run -N<n> -n<n> -c1 dftracer_service start <dir>` **never returns**. The daemon is
forked but remains a child of the flux task, so the task holds its node until the daemon
dies. A run wrapper that calls it this way blocks forever at that line and the application
phase never launches — with no error, and with the per-node
`dftracer_server_<hostname>.pid` files correctly written, so it looks like it worked.

Observed on dftracer 2.1.0.dev16 (develop, 2026-07-28) on Tuolumne/Flux: the inner job sat
`R` for 5+ minutes with all 4 pid files present and zero progress.

**Fix — launch it detached and poll for the pid files:**
```bash
SVC_JOB=$(flux submit -N$N -n$N -c1 dftracer_service start "$SVC_DIR")
for _ in $(seq 1 30); do
  [ "$(ls "$SVC_DIR"/dftracer_server_*.pid 2>/dev/null | wc -l)" -ge "$N" ] && break
  sleep 2
done
flux submit -N$N -n$N -c1 dftracer_service stop "$SVC_DIR"   # after the app phase
```

**Why:** this corrects [[feedback-dftracer-service-node-counters]], which prescribes
`flux run -N<n> -n<n> -c1`. The `-n<n> -c1` part (never `--tasks-per-node`, which silently
reserves the nodes exclusively) is still right; the `flux run` part is not. Also still true:
`DFTRACER_ENABLE` and `DFTRACER_LOG_FILE` must be exported in the same invocation or the
daemon silently no-ops, and a non-empty per-node trace must be verified afterwards.

**How to apply:** in every run-launch wrapper, submit the service detached before the app
phase and stop it in an `EXIT` trap. Never block the wrapper on `dftracer_service start`.
