# Flux Job Allocation

Allocate N nodes from an available Flux queue, then connect to the instance
via `flux proxy` to run jobs inside it.

## Step 1 — Discover available queues and resources

```bash
# List all queues with their status and limits
flux queue list

# Show free/idle nodes across queues
flux resource list -s free

# Check overall resource summary
flux resource info
```

Identify a queue with free nodes that fits the time constraint.

## Step 2 — Allocate nodes with `flux alloc`

```bash
# Basic: allocate N nodes interactively (opens a shell inside the instance)
flux alloc -N <N> -q <QUEUE> -t <TIME>

# With a bank (if the system uses flux-accounting)
flux alloc -N <N> -q <QUEUE> -B <BANK> -t <TIME>

# Background mode: allocate without attaching (returns a JOBID)
flux alloc --bg -N <N> -q <QUEUE> -t <TIME>

# Exclusive nodes, with a name tag
flux alloc -N <N> -q <QUEUE> -t <TIME> -x --job-name=<NAME>
```

**Key flags:**

| Flag | Meaning |
| --- | --- |
| `-N N` | Number of nodes |
| `-q NAME` | Queue name |
| `-B BANK` | Bank/account name (flux-accounting) |
| `-t MIN` or `-t 1.5h` or `-t 90m` | Time limit (minutes, or Flux Standard Duration) |
| `-x` | Exclusive node allocation |
| `--bg` | Return immediately with JOBID instead of attaching |
| `-n N` | Number of resource slots (alternative to `-N`) |
| `-c N` | Cores per slot |
| `-g N` | GPUs per slot |
| `--urgency=N` | Priority 0–31 (hold=0, default=16, expedite=31) |

## Step 3 — Connect to the allocated instance with `flux proxy`

When `flux alloc --bg` was used, connect to the running instance:

```bash
# Get the JOBID from: flux jobs -a | head
JOBID=$(flux jobs -a --no-header -o "{id}" | head -1)

# Connect to the instance (spawns a new shell inside it)
flux proxy $JOBID

# Or connect to a nested instance using jobid/child-jobid path
flux proxy $JOBID/$CHILD_JOBID

# Or if running inside Slurm/another RM
flux proxy slurm:<SLURM_JOBID>
```

Once inside the proxy shell, `FLUX_URI` is set and all `flux` commands
target that allocation. Exit the shell to disconnect.

## Step 4 — Run jobs inside the allocation

Inside the proxy shell (or from `flux alloc` interactive shell):

```bash
# Run a command across all allocated nodes
flux run -N <N> -n <NTASKS> <COMMAND>

# Run with GPUs per task
flux run -N <N> -n <NTASKS> -g 1 <COMMAND>

# Submit non-blocking
flux submit -N <N> -n <NTASKS> <COMMAND>

# Check running jobs
flux jobs

# Wait for all jobs to finish
flux queue idle
```

## Always use `--exclusive` when running multiple comparison jobs inside one allocation

Without `--exclusive`, `flux run`/`flux submit -N n -n n -c 1` lets Flux PACK multiple
independent jobs onto the same nodes if resources technically fit (e.g. two 2-node/8-GPU Ray
jobs both landing on the same 2 physical nodes, contending for the same 8 GPUs). This silently
corrupts any A/B optimization comparison — both jobs run slower and neither number means
anything. Confirmed 2026-07-25 (ray_molformer session): a duplicated/relaunched job instance
co-scheduled onto the exact same 2 nodes as a concurrently-running comparison variant. Always
pass `--exclusive` (or `-x` for `flux alloc`) for any job whose timing you intend to compare
against another, and verify via `flux jobs -a` that no two comparison runs share a node before
trusting either one's numbers.

## Baseline/optimization-variant timing comparisons MUST be interleaved, never cross-window

Confirmed independently on this system at least 3 times now (scaffold KB: 140.96s vs 293.46s
same config 30 min apart; ray_molformer: identical unmodified code measured 65s in one window
and 40s ~90 minutes later — a 35%+ apparent difference from system-load drift alone, with zero
code change). **Never trust a timing comparison between a baseline measured at one point in
time and a variant measured later** — system load (other users' jobs, shared filesystem
contention, thermal/frequency scaling) drifts enough on an HPC cluster to fabricate or hide a
real optimization effect. Always run one unmodified-baseline replicate INTERLEAVED (submitted
in the same time window, ideally back-to-back) with every variant you're comparing it against,
and treat any single-window "before/after" number as provisional until confirmed this way.

## Inspecting a job with `flux job info`

`flux job info <JOBID> <KEY>` dumps a specific piece of a job's stored data (the
`KEY` is a KVS guest key). Use it to introspect what a job actually requested,
what it was allocated, and its lifecycle events — invaluable for debugging a run
that failed, got the wrong resources, or when reconstructing an allocation's shape.

```bash
# Resource set actually assigned to the job (nodes, cores, ranks → hostnames)
flux job info <JOBID> R

# The jobspec that was submitted (requested nodes/tasks/cores, attributes, env)
flux job info <JOBID> jobspec

# Full eventlog: submit → depend → alloc → start → finish → release, with timestamps
flux job info <JOBID> eventlog

# Guest eventlog (shell/exec events inside the job, e.g. per-task exit codes)
flux job info <JOBID> guest.exec.eventlog
```

**Common keys:** `R` (allocated resources), `jobspec` (request), `eventlog`
(state transitions + exceptions/errors), `guest.exec.eventlog` (task-level exec
events). If a key is missing, the job hasn't reached that stage yet.

Related job-inspection commands (higher-level, human-formatted):

```bash
# One-line status + resources of a job
flux jobs <JOBID>

# Detailed, formatted view (state, exceptions, resources, node list)
flux job status -v <JOBID>

# Re-attach to a running/finished job to stream its stdout/stderr and get exit code
flux job attach <JOBID>

# Resolve node hostnames assigned to a job (parse R)
flux job info <JOBID> R | flux hostlist -
```

Inside a proxied allocation, prefix with `flux proxy <ALLOC_JOBID>`:

```bash
flux proxy <ALLOC_JOBID> flux job info <INNER_JOBID> eventlog
```

Use `flux job info <JOBID> eventlog` first when a run fails — the `exception`
entries there carry the actual failure reason (e.g. OOM, timeout, node failure).

## Decision logic (when user asks to allocate)

1. Run `flux queue list` to show available queues.
2. Run `flux resource list -s free` to see free node counts per queue.
3. Ask the user: how many nodes, how long, which queue (or auto-select
   the queue with the most free nodes that fits the time limit).
4. Run `flux alloc --bg -N <N> -q <QUEUE> -t <TIME>` and capture JOBID.
5. Run `flux proxy <JOBID>` to hand control to the user inside the allocation,
   or use `flux run` commands inside the proxy session to execute work.

## Auto-scaling: use all nodes and physical cores

When executing runs (smoke tests, dftracer runs, benchmarks) inside a flux proxy allocation:

1. **Always use all available nodes** in the allocation unless the user specifies fewer.
2. **Always use all physical cores per node** for MPI tasks — do not leave cores idle.

```bash
# Discover allocation size and compute task count at runtime
N_NODES=$(flux resource list --format="{nnodes}" -s free 2>/dev/null | awk 'NR>1{print $1}' | head -1)
CORES_PER_NODE=96   # tuolumne: 96 physical cores per MI300A node
N_TASKS=$((N_NODES * CORES_PER_NODE))

flux run -N $N_NODES -n $N_TASKS <CMD>
```

When proxying into an existing allocation with `flux proxy <JOBID> flux run ...`:

```bash
flux proxy <JOBID> flux run \
  -N 8 -n 768 \         # use all nodes × 96 cores
  --env LD_LIBRARY_PATH=<mpi_lib>:<cce_lib>:<app_libs> \
  <CMD>
```

Always pass DFTRACER and LD_LIBRARY_PATH env vars explicitly with `--env` flags — they are NOT automatically inherited by compute nodes via flux proxy.

## Env var pitfall: special characters in --env values

**NEVER pass env vars containing semicolons, asterisks, or other shell metacharacters
directly via `--env` to `flux proxy ... flux run`.** `flux proxy` parses the
remaining command-line arguments and treats semicolons as command separators,
silently dropping the binary argument and printing:

```text
flux-run: ERROR: job command and arguments are missing
```

This affects any value with `;` — including ROMIO/MPICH_MPIIO_HINTS, module
load strings, and any colon-separated list with special chars mixed in.

**Always use a wrapper script instead:**

```bash
# WRONG — semicolons in --env value cause flux-run to lose the command:
flux proxy $JOB flux run -N 2 -n 192 \
  --env 'MPICH_MPIIO_HINTS=*:romio_cb_write=enable;cb_buffer_size=64m' \
  /path/to/binary args...    # ← binary is silently dropped

# CORRECT — put complex env vars in a wrapper script:
cat > /path/to/wrapper.sh << 'EOF'
#!/bin/bash
export MPICH_MPIIO_HINTS="*:romio_cb_write=enable;cb_buffer_size=67108864;cb_nodes=16"
export MY_OTHER_COMPLEX_VAR="a=1;b=2;c=3"
exec "$@"
EOF
chmod +x /path/to/wrapper.sh

flux proxy $JOB flux run -N 2 -n 192 \
  --env LD_LIBRARY_PATH=<libs> \
  --env DFTRACER_ENABLE=1 \
  bash /path/to/wrapper.sh /path/to/binary args...
```

**Rule: use `--env` only for simple key=value pairs with no semicolons, asterisks,
or other metacharacters. For anything complex, write a wrapper script.**

Note: `MPICH_MPIIO_HINTS` uses **colon-separated** key=value pairs (not semicolons):
`"*:romio_cb_write=enable:cb_buffer_size=67108864:cb_nodes=16"` — see `software-mpi` skill
for the full list of Cray MPICH 9.0.1 supported hints and how to discover them on new versions.

## Tuolumne-specific notes

On tuolumne, use `flux_wrappers` module — the `flux` command is already
wrapped to handle the CORAL2-specific flags:

```bash
# Tuolumne-specific: allocate with chassis distribution
flux alloc -N <N> -q <QUEUE> -t <TIME> --coral2-chassis=<C>

# Set GPU compute partition mode (CPX/TPX/SPX)
flux alloc -N <N> -q <QUEUE> -t <TIME> --amd-gpumode=CPX
```

## Never queue more jobs than the allocation can run concurrently (MANDATORY)

**Before submitting ANY job into an allocation, check how many jobs are already queued/running in
it and never exceed the number that can execute concurrently.** An 8-node allocation can run
exactly ONE 8-node/768-rank job at a time — submitting a second one before the first completes
just queues it, hogging no compute but polluting the job queue and (if a retry loop doesn't wait
for completion before resubmitting) can runaway into thousands of queued jobs.

**Why:** on 2026-07-10, a submission loop that didn't wait for job completion (and didn't cancel
a stale/timed-out job) before retrying left 5 of 8 allocations with 600-760 queued `S`-state jobs
each (~3,200 total) — all requesting the full allocation, so only one could ever run per
allocation regardless of how many were queued. Recovered via individual, scoped
`flux proxy <alloc> flux cancel <jobid>` calls, one per stuck job in each affected allocation —
**never `flux cancel --all`, even scoped to a single allocation** (confirmed separately as its
own incident in `feedback-h5bench-session-incidents`: bulk cancellation risks destroying other
jobs sharing that same allocation that were never meant to be touched). See "Cancelling a job or
allocation" above for the full standing rule.

**How to apply:**
1. Before submitting, check occupancy: `flux proxy <alloc-id> flux jobs -a | grep -cE ' R | PD | S '`.
   If it's already at (or above) the allocation's concurrent capacity, wait for the running job to
   finish (poll, don't just resubmit) before submitting another.
2. If a job appears stuck/timed-out, explicitly cancel it (`flux proxy <alloc-id> flux cancel <id>`)
   before resubmitting — never submit a retry "on top of" a job you haven't confirmed is done.
3. Never submit in a bare loop without a completion check between iterations — that is exactly
   the pattern that caused the pileup above.

## `--exclusive` on two co-scheduled jobs targeting the same nodes deadlocks (MANDATORY)

**Never pass `--exclusive` to more than one `flux run` that must run concurrently on the same node
set inside an allocation.** `--exclusive` means "give this job sole ownership of these nodes,
don't co-schedule anything else on them" — if a helper/sidecar job (e.g. a per-node monitoring
daemon meant to run *alongside* the main job) requests `--exclusive` on the same N nodes the main
job also needs, the scheduler will hold the first job's nodes and leave the second job stuck in
`S` (pending) forever, since neither job's exclusive request can be satisfied while the other
holds the nodes.

**Why:** confirmed on a vpic-kokkos 8-node run (2026-07-14) — a per-node `dftracer_service`
daemon job launched with `--exclusive -N8 -n8` and backgrounded via `&` correctly started, but the
main `benchmark.Linux` job (`--exclusive -N8 -n128`, same 8 nodes) sat in `S` state indefinitely
behind it. Diagnosed by checking `flux proxy <alloc> flux jobs -a` and seeing the app job stuck
`S` while the sidecar job showed `R`.

**How to apply:** inside an allocation that is *already* exclusive to you (from `flux alloc`),
don't add `--exclusive` to the individual `flux run` calls for jobs meant to co-exist on the same
nodes (sidecar daemons + the main app run) — drop it from all of them. Reserve `--exclusive` on an
individual `flux run` only when that job genuinely must be the sole occupant of the nodes it
targets (e.g. a benchmark run inside a shared/non-exclusive allocation).

## Full-allocation parallel job spawner

When the user asks to "use all available resources" or "spawn jobs to use the entire allocation":

### 1. Discover allocation size at runtime

```bash
TOTAL_NODES=$(flux proxy $FLUX_JOB flux resource list -s free --format="{nnodes}" 2>/dev/null \
  | awk 'NR>1{s+=$1}END{print s}')
[ -z "$TOTAL_NODES" ] && TOTAL_NODES=4   # fallback if resource list fails
CORES_PER_NODE=96   # tuolumne: 96 physical cores per MI300A node
NODES_PER_JOB=$(( TOTAL_NODES / 2 ))
TASKS_PER_JOB=$(( NODES_PER_JOB * CORES_PER_NODE ))
echo "Allocation: $TOTAL_NODES nodes → 2 jobs × ${NODES_PER_JOB}N × ${TASKS_PER_JOB} tasks each"
```

### 2. Generic parallel launcher template

Run 2 jobs simultaneously in pairs, wait, then start the next pair.

```bash
#!/bin/bash
# run_parallel_all.sh — use entire flux allocation, 2 jobs at a time

FLUX_JOB=<JOBID>
CORES_PER_NODE=96
LDPATH="<full LD_LIBRARY_PATH>"
LOGDIR="<log directory>"

TOTAL_NODES=$(flux proxy $FLUX_JOB flux resource list -s free --format="{nnodes}" 2>/dev/null \
  | awk 'NR>1{s+=$1}END{print s}')
[ -z "$TOTAL_NODES" ] && TOTAL_NODES=4
NODES_PER_JOB=$(( TOTAL_NODES / 2 ))
TASKS_PER_JOB=$(( NODES_PER_JOB * CORES_PER_NODE ))

run_bg() {
  local name=$1; shift
  flux proxy $FLUX_JOB flux run \
    -N $NODES_PER_JOB -n $TASKS_PER_JOB \
    --env "LD_LIBRARY_PATH=$LDPATH" \
    "$@" > "$LOGDIR/${name}.log" 2>&1 &
  echo $!
}

# Define workloads as pairs; last one runs solo if odd count
declare -a NAMES=( workload_a  workload_b  workload_c  workload_d )
declare -a CMDS=(
  "<cmd_a> <args>"
  "<cmd_b> <args>"
  "<cmd_c> <args>"
  "<cmd_d> <args>"
)

i=0
while (( i < ${#NAMES[@]} )); do
  NA="${NAMES[$i]}"; CA="${CMDS[$i]}"; i=$(( i+1 ))
  if (( i < ${#NAMES[@]} )); then
    NB="${NAMES[$i]}"; CB="${CMDS[$i]}"; i=$(( i+1 ))
    echo "=== PAIR: $NA + $NB ==="
    PA=$(run_bg "$NA" $CA); PB=$(run_bg "$NB" $CB)
    wait $PA && echo "$NA DONE" || echo "$NA FAILED"
    wait $PB && echo "$NB DONE" || echo "$NB FAILED"
  else
    echo "=== SOLO: $NA ==="
    PA=$(run_bg "$NA" $CA)
    wait $PA && echo "$NA DONE" || echo "$NA FAILED"
  fi
done
echo "=== ALL COMPLETE ==="
```

### 3. Decision rules

| Condition | Action |
|-----------|--------|
| `TOTAL_NODES == 1` | 1 job, all cores (`-N 1 -n 96`) |
| `TOTAL_NODES == 2` | 1 job at a time (`-N 2 -n 192`) |
| `TOTAL_NODES == 4` | 2 jobs × 2 nodes × 96 = 192 tasks each |
| `TOTAL_NODES == 8` | 2 jobs × 4 nodes × 96 = 384 tasks each |
| `TOTAL_NODES % 2 != 0` | `NODES_PER_JOB = TOTAL_NODES / 2` (floor); odd workload count → last runs solo |

### 4. dftracer env flags (add to every `flux run`)

```bash
--env DFTRACER_ENABLE=1 \
--env DFTRACER_INIT=FUNCTION \
--env DFTRACER_INC_METADATA=1 \
--env DFTRACER_DATA_DIR=all \
--env "DFTRACER_LOG_FILE=$TRACES/${name}" \
```

### 5. OS cache avoidance requirement (R9)

Each job must write > 50% of its allocated nodes' physical RAM to the filesystem.
Tuolumne: MemTotal ≈ 502 GiB/node.

- 2-node job: threshold > 502 GiB total → use `DIM_1=33554432` (768 GiB for 192 ranks) ✓
- `DIM_1=16777216` gives 384 GiB for 192 ranks → does NOT bypass OS cache ✗

## Cancelling a job or allocation (NEVER the allocation itself, NEVER `--all`)

When a job is killed, crashes, or needs to be stopped, cancel ONLY that job's own
Flux job ID — the one submitted *within* an allocation via `flux run`/`flux submit`.
**Forgetting to cancel a killed job leaks the allocation's concurrent-job slot and
may block other runs**, but the fix is always scoped to the job, never the
allocation.

```bash
# Cancel a specific job inside the allocation (via proxy):
flux proxy <ALLOC_JOBID> flux cancel <INNER_JOBID>

# List running jobs to find IDs:
flux proxy <ALLOC_JOBID> flux jobs -a
```

**NEVER cancel the allocation itself** (`flux cancel <ALLOC_JOBID>` on a top-level
`flux batch`/`flux alloc` job, typically shown as `NAME=flux` in `flux jobs -a` with
the full requested node count). Allocations are the scarce, slow-to-acquire resource
on a shared HPC scheduler — tearing one down forces requeueing, costs significant
wait time, and is disruptive to other work sharing the cluster. Confirmed incident:
the user explicitly corrected an agent that ran `flux cancel` on several top-level
allocation jobs — including their own pre-existing one — while trying to clean up
what it thought were stray/duplicate allocations. See
`feedback-flux-allocation-vs-job` for the full incident. Separately: "run the job on
N nodes" means an N-node job submitted *inside* an existing allocation, not a fresh
N-node allocation — use whatever allocation is already available/running (even if
it has more nodes than N).

**NEVER use `flux cancel --all`**, even to clean up a confirmed runaway
job-submission loop. Bulk/global cancellation on a shared multi-tenant cluster risks
destroying other users' or unrelated sessions' jobs and allocations. Confirmed
incident: a submission loop that didn't wait for job completion before retrying left
~3,200 queued jobs stacked across 5 allocations; recovery was done via individual,
scoped `flux cancel <jobid>` calls per stuck job — one call per job, never a bulk
`--all` sweep even scoped to a single allocation. See
`feedback-h5bench-session-incidents` for the full incident.

**When to cancel a job (not the allocation):**
- Any `flux run` or `flux submit` job that was killed with Ctrl-C, `kill`, or crashed
- Any background job (`&`) whose PID is dead but the flux job is still listed
- Before re-running a failed benchmark to avoid stale job conflicts
- A confirmed runaway/duplicate job found via `flux jobs -a` — cancel its specific
  job ID, one call per stuck job, even if that means many individual calls


## Permissions

This skill uses:

- **Bash:** `flux` (alloc / run / submit / proxy / cancel / jobs), `module` — always through a bash wrapper script, never inline module loads
- **Write:** `workspaces/<session>/*` only (job scripts, wrapper scripts, logs)

Always cancel killed/crashed Flux job IDs to release the allocation. Never `sudo`; never write outside the project root.

## Allocations: ASK the user first (baseline and optimization runs)

Before any baseline or optimization run that needs nodes, ASK the user which they want:

1. **Use an existing user-created allocation** via `flux proxy <JOBID> bash <wrapper>.sh ...`
   (the user often keeps a standing allocation running; this is frequently the preference), or
2. **Spawn a new allocation** (`flux batch -N <n> -q pdebug -t <mins> --wrap "bash <wrapper>.sh ..."`).

Do not assume. If the user has named a JOBID, prefer it, and check its remaining time with
`flux jobs -no "{id} {state} {t_remaining}" <JOBID>` before starting — a run that outlives the
allocation is lost work.

**Never block on a long `flux proxy` in the foreground.** The Bash tool caps at ~10 minutes and
killing the proxy client kills the job inside the allocation. Launch it with
`run_in_background: true` (or `flux submit` inside the allocation) and poll.

Queue note: 8-node `pbatch` jobs may sit in SCHED indefinitely; `pdebug` usually schedules at once.

**Use the scheduler's own ETA to pace polling instead of guessing an interval.** A job still in
`SCHED` state carries the scheduler's own start-time estimate in its annotations —
`flux jobs -no "{id} {annotations.sched.t_estimate}" <JOBID>` returns a Unix epoch timestamp
(the same number the human-readable `INFO` column shows as `eta:24.32m` in default `flux jobs`
output). Compute minutes-until-start as `(t_estimate - time.time()) / 60` and set your poll
delay close to that, rather than polling every few minutes blind — e.g. if the estimate says
24 minutes out, don't check back in 3-5 minute increments; wait closer to the estimate (leaving
a little buffer since estimates can move) and re-check. `t_estimate` itself moves over time as
the scheduler re-plans, so treat it as a moving target you re-read each time, not a fixed
deadline.

## Run length: make the run long enough to measure

A run whose training phase is a few seconds cannot resolve checkpoint, collective, or barrier
effects — the deltas are inside run-to-run noise. Target **at least ~10 minutes of training**.

## Replicates and percentile reporting (MANDATORY, standing rule — supersedes "one replicate")

Lustre contention and network noise can make a single run's number an outlier. This is a
**standing rule for every benchmark run used as a baseline OR as an optimization-comparison
point** — not just for one session's plan, and not satisfied by "at least one replicate."

1. **Minimum 5 replicates.** Every baseline run and every optimization-iteration run that
   will be compared or reported must be repeated a **minimum of 5 times** under the same
   configuration.
2. **CV-adaptive escalation.** Compute the coefficient of variation (CV = stddev / mean) on
   the primary throughput/bandwidth metric across replicates. If CV > ~10-15%, the noise band
   is too wide to trust — increase to **8 replicates**, then to **10 replicates**, re-checking
   CV each time, until it stabilizes below that band (or until 10 is reached, in which case
   report the residual CV honestly rather than hiding it).
3. **Percentile reporting, not a bare number.** Always report the metric as **p50 (median),
   p95, min, and max** across the replicate set — never a single sample. A "best of N" or
   "last run" number is not an acceptable substitute.
4. **Percentile-based improvement claims only.** Any improvement/regression claim between a
   baseline and an optimization variant must compare percentiles (e.g. "median improved 12%,
   p95 improved 8%"), never a single-sample delta ("run A: 3.2 GB/s vs run B: 3.6 GB/s" is not
   a valid claim on its own). Report the delta against the noise band established by the
   replicate set, exactly as the general run-length guidance above says.

## Verify scale and completion before crediting ANY wall-time delta

Confirmed on PECAN (2026-07-20): an optimizer subagent launched its opt1 variant at HALF the
baseline's scale (2 nodes/8 ranks via a leftover `run_opt1_2n.sh` script instead of the
intended 16-rank `run_opt1.sh`) and the run was ALSO cancelled mid-flight
(`job.exception cancel` in the flux event log) when the shared allocation died — producing
truncated `.pfw.gz` traces. The comparator tool dutifully reported a number (31.4s/38proc
baseline vs 7.0s/16proc opt1) that LOOKED like a measurement but was actually confounded by
both scale and truncation simultaneously — not creditable as a wall-time result.

**Before comparing any optimization variant against baseline, verify:**
1. **Rank/process count matches** — check the comparator's own process-count field, or
   `flux job info <jobid> R` / the run script actually invoked (grep the launch log for which
   `.sh` ran, don't assume from the filename you *meant* to use).
2. **No `job.exception cancel`** (or any non-zero exit) in the run's flux event log
   (`flux job info <jobid> eventlog`).
3. If either check fails, DO NOT report a wall-time delta — fall back to a scale- and
   truncation-robust work-normalized metric instead (e.g. opens-per-`__getitem__`,
   ops-per-sample, bytes-per-record) which stays valid even from a partial/wrong-scale run,
   and explicitly flag that a clean equal-scale completed re-run is still needed to quantify
   wall-time impact. Report the mechanism as validated and the wall-time speedup as
   NOT YET MEASURED — never silently launder a confounded number into a clean-looking delta.

## Trace-processing MCP tools: pass `allocation_id` explicitly (MANDATORY)

The dftracer MCP tools that scan/merge/compress/analyze large `.pfw`/`.pfw.gz` trace
directories — `analyze`, `split`, `event_count`, `merge`, `reader`, `comparator`,
`aggregator`, `aggregator_mpi`, `stats`, `view`, `index`, `organize`, `reconstruct`,
`call_tree`, `call_tree_mpi`, `pgzip`, `tar` — all run their underlying binary via a
shared helper that auto-detects a live Flux allocation and runs the command there
(`flux proxy <jobid> flux run ...`) instead of on the MCP server's own (often shared
login/service) host. This auto-detection only works when there's exactly one relevant
allocation running under the current user, and picks whichever has the most walltime
remaining — which may not be the one the agent actually means to use.

**Every agent that calls one of these tools on a session with a live allocation MUST**:

1. **Confirm an allocation exists** before the call — check session state for a
   recorded JOBID (from an earlier `session_run_with_dftracer(allocation_id=...)` call
   or a `flux_alloc` this session made), or run `flux jobs -a` to check.
2. **Pass `allocation_id=<jobid>` explicitly** to the tool call rather than relying on
   auto-detection — this is the only way to guarantee the RIGHT allocation is used when
   more than one may be running (e.g. a validation-run allocation vs. a leftover one from
   an earlier step), and it skips an extra `flux jobs` round-trip per call.
3. **If no allocation exists** and the trace directory is large (many files / large
   total size), ask the user for one (per "Allocations: ASK the user first" above)
   rather than letting the tool silently fall back to running on the MCP server's host —
   that fallback exists for correctness/availability, not as the default for large scans.
4. **Small, quick checks are fine without an allocation** — a single small trace file's
   `event_count`, or a `stats`/`view` query against an already-compacted small trace, does
   not need to wait on an allocation. Use judgment on "large" the same way you would for
   choosing whether a smoke test needs a compute node.

```python
# Example: agent already has run_id's allocation from session state
mcp__dftracer__analyze(trace_path=compact_dir, analyzer_preset="generic",
                        allocation_id=session_jobid)
mcp__dftracer__event_count(directory=compact_dir, allocation_id=session_jobid)
```

If `allocation_id` is omitted, the tool still works (auto-detect, then local fallback) —
but explicit is preferred so multi-allocation ambiguity never silently picks the wrong one.

## PyTorch Lightning / torch.distributed DDP on a non-`--exclusive` single-task `flux run` can fork-bomb the node

**Generic gotcha, not app-specific** (first hit on MoLFormer, see `software-molformer` for the
full writeup): Flux's built-in PMI shim sets `PMI_RANK=0`/`PMI_SIZE=1`/`PALS_*`/`LDCS_RANKINFO`
in the environment for ANY `flux run`, even a plain single-task, non-MPI one. Any
multi-GPU-per-node framework whose subprocess-based launcher (PyTorch Lightning's `ddp`
strategy, raw `torch.distributed` subprocess launch, etc.) uses those PMI vars to decide "am I
an already-spawned child" can get stuck: every spawned child inherits the SAME `PMI_RANK=0`
unchanged, so none of them recognize themselves as a child, and each one re-triggers spawning
its siblings — recursively, without bound. This tends to stay latent (silent, zero output) until
combined with a real error condition — e.g. requesting more GPUs than the job's resource slice
actually grants (which itself happens when `--exclusive` is dropped from the `flux run`/
`flux submit`, since a fair-share task-count-based slice may only see 1 GPU instead of all N on
the node).

**Fix:** always launch node-local multi-GPU DDP/distributed training with `--exclusive` (needs
the whole node's GPUs), AND unset the Flux/Cray PMI vars before invoking the training process:
`unset PMI_RANK PMI_SIZE PMI_FD PALS_APID PALS_APINFO PALS_NODEID PALS_RANKID PALS_SPOOL_DIR LDCS_RANKINFO`.

**Detection:** if a job shows zero stdout/output for many minutes with no error, check the
node's process count before assuming it's just slow:
`flux proxy <alloc> flux run -N1 -n1 --requires=hosts:<node> ps aux | grep -c <script_name>`.
A rapidly-growing count means cancel immediately
(`flux proxy <alloc> flux cancel <jobid>`) rather than waiting it out.
