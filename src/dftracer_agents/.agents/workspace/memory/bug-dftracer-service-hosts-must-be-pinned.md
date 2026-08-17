---
name: bug-dftracer-service-hosts-must-be-pinned
description: In an allocation bigger than the job, dftracer_service start / app / stop each get a DIFFERENT node subset — daemons profile the wrong nodes, stop says "No running server found", and service traces stay 0 bytes
metadata:
  type: feedback
---

Inside a flux allocation larger than the job you are running, three separate
`flux submit -N<n>` calls get three **different** n-node subsets. Observed while running a
4-node job inside a 32-node allocation: `dftracer_service start`, the application, and
`dftracer_service stop` each landed on a different set of 4 nodes.

Every consequence is silent:

- the daemons profiled nodes the application never ran on, so the node counters describe
  the wrong machines;
- `stop` reported `No running server found.` for nodes whose daemons it had not started;
- the un-stopped daemons kept running as orphans, each holding a core;
- their `service_<host>.pfw.gz` files stayed **0 bytes**, because the trace is only
  flushed on stop.

Nothing errors. You get a complete-looking run with four empty node-counter traces and
leaked daemons.

**Fix — pin all three jobs to the SAME explicit hosts:**

```bash
flux jobs -a --format="{id} {name} {status} {nnodes} {nodelist}" | grep RUN  # who else is here
REQ="--requires=host:<nodeA>,<nodeB>,<nodeC>,<nodeD>"
flux submit -N4 -n4  -c1  $REQ --setattr=exclusive=false "$DFT_BIN" start "$SVC_DIR"
flux run    -N4 -n16 -g1 -c23 $REQ --setattr=exclusive=false ./app_launch.sh
flux submit -N4 -n4  -c1  $REQ --setattr=exclusive=false "$DFT_BIN" stop  "$SVC_DIR"
```

Never assume the whole allocation is yours — it may be carrying another live session, and
`flux resource list` shows the *cluster*, not the allocation's internal occupancy.

**Why:** this compounds [[bug-dftracer-service-start-blocks-flux-run]] (start must be
`flux submit` detached, not `flux run`) and [[feedback-dftracer-service-node-counters]]
(`-n<n> -c1`, never `--tasks-per-node`). Those two get the daemon running; this one gets it
running *on the right nodes*. Note also that the MCP `session_service_start` tool starts a
single daemon on whichever host the MCP server itself runs on, so it cannot express a
per-compute-node launch for a multi-node job — the run wrapper has to do it.

**How to apply:** resolve the host list once at the top of the run wrapper and reuse it for
start, app and stop. Afterwards verify one **non-empty** `service_<host>.pfw.gz` per node
in the job; a 0-byte one means a daemon was never stopped. Recover orphans by stopping
each on its own host: `flux run -N1 -n1 -c1 --requires=host:<h> ... stop "$SVC_DIR"`.

Session context: [[project-minife-papi-hip-dftracer-baseline]].
