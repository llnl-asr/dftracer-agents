#!/bin/bash
# Genesis sweep WORKER -- runs INSIDE a flux OR slurm allocation
# (GENESIS_SCHED, auto-detected in genesis_config.sh).
#
# Executes every not-yet-completed (input, nodes, ppn, papi_set) run, packing
# independent runs onto DISJOINT host slices of the allocation so that small
# node-counts use the whole allocation instead of one node at a time.
#
# HARD RULES encoded here, each learned the hard way:
#  * service start / app / service stop must be pinned to the SAME hosts with
#    --requires=host:. In an allocation larger than the job, three separate
#    submits otherwise land on three different node subsets and the daemons
#    profile the wrong nodes (service traces come back 0 bytes).
#  * `dftracer_service start` under `flux run` NEVER returns -- it must be
#    `flux submit` (detached) and then polled via its per-host pid files.
#  * one service instance per node, pinned to ONE core (-N n -n n -c1), never
#    --tasks-per-node, so it does not compete with application ranks.
#  * checkpoint AFTER verifying the artifacts, never on exit status alone.
set -uo pipefail

WS=${GENESIS_WS:?set GENESIS_WS to the session workspace}
_G=${GENESIS_G:-$WS/genesis/${GENESIS_SYSTEM:-$(hostname | sed 's/[0-9]*$//')}}
# THIS system's env + config, never the session's.
source "$_G/env.sh"                   > /dev/null 2>&1
source "$_G/scripts/genesis_config.sh"

# Deadline. The worker only starts executing once the allocation is GRANTED, so
# "my start time + the allocation's -t budget" is an accurate expiry even though
# the driver cannot know how long the request sat in the queue.
#
# Getting this right matters: with no deadline the worker runs until flux kills
# the allocation mid-run, which throws away every in-flight run (they are not
# checkpointed until they validate) and leaves partial trace dirs behind. With
# it, the worker stops dispatching in time, exits 7, and the driver simply asks
# for the next allocation and resumes from the checkpoint.
ALLOC_SECONDS=${GENESIS_ALLOC_SECONDS:-3480}     # matches the driver's -t 58m
DEADLINE=$(( $(date +%s) + ALLOC_SECONDS - 60 ))
# Prefer the scheduler's real expiration; fall back to the budget (fail SAFE:
# a wrong-short deadline only ends the allocation early, never mid-run).
_exp=""
if [ "$GENESIS_SCHED" = flux ]; then
  _exp=$(flux --parent job info "${FLUX_JOB_ID:-}" R 2>/dev/null \
         | python3 -c 'import json,sys; print(int(json.load(sys.stdin)["execution"]["expiration"]))' 2>/dev/null)
elif [ -n "${SLURM_JOB_ID:-}" ]; then
  _e=$(squeue -h -j "$SLURM_JOB_ID" -o %e 2>/dev/null)
  [ -z "$_e" ] && _e=$(scontrol show job "$SLURM_JOB_ID" 2>/dev/null | grep -oP 'EndTime=\K\S+')
  [ -n "$_e" ] && _exp=$(date -d "$_e" +%s 2>/dev/null)
fi
if [ -n "$_exp" ] && [ "$_exp" -gt 0 ] 2>/dev/null; then
  DEADLINE=$(( _exp - 60 ))
  echo "deadline from $GENESIS_SCHED: $DEADLINE ($(( (DEADLINE - $(date +%s)) / 60 )) min of allocation left)"
else
  echo "deadline from budget: $DEADLINE ($(( ALLOC_SECONDS / 60 )) min budget, scheduler expiration unavailable)"
fi

# LOGDIR comes from genesis_config.sh ($G/artifacts): per-system, so one
# machine’s run logs never land in another machine’s artifacts directory.
mkdir -p "$LOGDIR" "$GENESIS"
touch "$STATE"

if [ "$GENESIS_SCHED" = slurm ]; then
  mapfile -t HOSTS_ALL < <(scontrol show hostnames "${SLURM_JOB_NODELIST:-}" 2>/dev/null)
else
  mapfile -t HOSTS_ALL < <(flux hostlist -e local 2>/dev/null | tr ' ' '\n' | grep -v '^$')
fi
NALLOC=${#HOSTS_ALL[@]}
[ "$NALLOC" -eq 0 ] && { echo "FATAL: no hosts in allocation"; exit 1; }
echo "=== genesis worker ($GENESIS_SCHED): $NALLOC nodes: ${HOSTS_ALL[*]} ==="

# ONE worker per state file (flock): two workers sharing a checkpoint pick the
# same keys and destroy each other's raw/ directories.
exec 9>"${STATE}.lock"
if ! flock -n 9; then
  echo "=== another worker already owns $(basename "$STATE") -- exiting (guard working) ==="; exit 0
fi
echo "=== acquired $(basename "$STATE").lock ==="
APP_TIMEOUT=${GENESIS_APP_TIMEOUT:-1800}
# Completion marker the app prints on a healthy run (rc==0 is not completion).
APP_MARKER=${GENESIS_APP_MARKER:-}

MAXOBS=90        # observed worst-case run wall time, grows as we measure

# ---------------------------------------------------------------- one run ----
run_one() {
  local nx=$1 N=$2 P=$3 setname=$4 events=$5 hostcsv=$6
  local key; key=$(run_key "$nx" "$N" "$P" "$setname")
  local leaf; leaf=$(leaf_dir "$nx" "$N" "$P")
  local raw="$leaf/raw/papi_${setname}"
  local svc="$raw/dftracer_service"
  local rundir="$G/dataset/genesis/$key"      # app output -> this system's PFS
  local wrap="$G/tmp/_genesis_${key}.sh"
  local args; args=$(case_args "$nx")
  local flag="$LOGDIR/.stop_${key}"
  local log="$LOGDIR/${key}.log"
  local ntasks=$(( N * P ))
  local req="--requires=host:$hostcsv"

  rm -rf "$raw"; mkdir -p "$svc" "$rundir" "$G/tmp"; rm -f "$flag"

  export DFTRACER_ENABLE=1
  export DFTRACER_INIT=FUNCTION                 # FUNCTION mode always, never PRELOAD
  export DFTRACER_LOG_FILE="$raw/$key"
  export DFTRACER_DATA_DIR=all
  export DFTRACER_TRACE_COMPRESSION=1
  export DFTRACER_INC_METADATA=1
  export DFTRACER_ENABLE_PAPI_TRACING=1
  export DFTRACER_PAPI_SAMPLE_INTERVAL_MS=100
  export DFTRACER_PAPI_EVENTS="$events"
  export DFTRACER_PAPI_MULTIPLEX=0
  # GPU tracing: ROCProfiler is runtime-gated; CUPTI is compiled in (no env).
  if [ "$GENESIS_GPU" = hip ]; then export DFTRACER_ENABLE_HIP_TRACING=1; else unset DFTRACER_ENABLE_HIP_TRACING; fi

  # Selective aggregation, if this system folder defines rules. Capture-time only:
  # it cannot be applied or undone afterwards, so a sweep either has it or does not.
  # DFTRACER_AGGREGATION_TYPE is compared CASE-SENSITIVELY against "SELECTIVE";
  # any other spelling silently means FULL, which aggregates EVERYTHING.
  if [ -n "${GENESIS_AGG_FILE:-}" ] && [ -f "${GENESIS_AGG_FILE}" ]; then
    export DFTRACER_ENABLE_AGGREGATION=1
    export DFTRACER_AGGREGATION_TYPE=SELECTIVE
    export DFTRACER_AGGREGATION_FILE="${GENESIS_AGG_FILE}"
  else
    unset DFTRACER_ENABLE_AGGREGATION DFTRACER_AGGREGATION_TYPE DFTRACER_AGGREGATION_FILE
  fi
  # variorum power on (service side) only where it works; on e.g. a 2-socket
  # node with a 1-core-pinned service variorum calls exit() (system skill).
  if [ "$GENESIS_POWER" = variorum ]; then unset DFTRACER_DISABLE_VARIORUM_POWER
  else export DFTRACER_DISABLE_VARIORUM_POWER=1; fi

  {
    echo "### $key hosts=$hostcsv ntasks=$ntasks counters=$events args=$args sched=$GENESIS_SCHED"
    echo "### aggregation=${DFTRACER_AGGREGATION_TYPE:-none} file=${DFTRACER_AGGREGATION_FILE:-none}"
    date +%s
  } > "$log"

  local t0; t0=$(date +%s)

  # KNOWN, MEASURED, NON-FATAL: the service process maps BOTH librocm_smi64.so.1
  # (via libhwloc.so.15) and librocm_smi64.so.7 (via libvariorum.so). Two SONAMEs
  # exporting the same symbols -> ELF interposition routes calls into one copy
  # while each keeps its own state, and the service aborts at TEARDOWN with
  # "corrupted size vs. prev_size in fastbins".
  # HWLOC_COMPONENTS=-rsmi does NOT prevent this (measured: both ABIs still map,
  # because .so.1 arrives through libhwloc's link, not a runtime plugin choice).
  # It is harmless here: the abort happens strictly AFTER the trace is flushed.
  # Verified on a crashed run -- gzip intact, 0 malformed lines, all categories
  # present including variorum `gpu`/power. The per-run verify below is what
  # actually gates each run, so a lost service trace could never be recorded ok.
  local svc_pid=""
  if [ "$GENESIS_SCHED" = slurm ]; then
    # Slurm: a daemon's lifetime is its job STEP, so the service runs as ONE
    # long-lived step (start -> wait on flag -> stop -> wait for a COMPLETE
    # gzip). --overlap co-schedules with the app, --nodelist pins it to this
    # run's slice, --wait=0 stops srun SIGKILLing the slower node's daemon 30s
    # after the first finishes (WaitTime) mid-flush. Optional NVML sampler.
    ( export DFTRACER_ENABLE=1 DFTRACER_LOG_FILE="$raw/service" DFTRACER_TRACE_INTERVAL_MS=500
      export DFT_SVC_DIR="$svc" DFT_SVC_BIN="$DFT_BIN" DFT_STOP_FLAG="$flag" DFT_POWER="$GENESIS_POWER"
      srun --jobid="$SLURM_JOB_ID" -N"$N" -n"$N" -c1 --nodelist="$hostcsv" \
           --overlap --wait=0 --quiet bash -c '
        H=$(hostname)
        "$DFT_SVC_BIN" start "$DFT_SVC_DIR" >/dev/null 2>&1
        if [ "$DFT_POWER" = nvml ]; then
          ( while [ ! -f "$DFT_STOP_FLAG" ]; do
              nvidia-smi --query-gpu=index,power.draw,utilization.gpu,memory.used \
                         --format=csv,noheader,nounits 2>/dev/null | sed "s/^/$(date +%s.%N),$H,/"
              sleep 0.5; done ) > "$DFT_SVC_DIR/gpu_power_$H.csv" 2>/dev/null &
        fi
        while [ ! -f "$DFT_STOP_FLAG" ]; do sleep 1; done
        wait
        "$DFT_SVC_BIN" stop "$DFT_SVC_DIR" >/dev/null 2>&1
        T="${DFTRACER_LOG_FILE}_${H}.pfw.gz"
        for _i in $(seq 1 180); do [ -s "$T" ] && gzip -t "$T" 2>/dev/null && break; sleep 1; done
        gzip -t "$T" 2>/dev/null || echo "WARN: service trace $T incomplete after 180s" >&2
      ' ) >> "$log" 2>&1 &
    svc_pid=$!
  else
    DFTRACER_ENABLE=1 DFTRACER_LOG_FILE="$raw/service" \
      flux submit -N"$N" -n"$N" -c1 $req --setattr=exclusive=false \
        "$DFT_BIN" start "$svc" >> "$log" 2>&1
  fi
  local i
  for i in $(seq 1 40); do
    [ "$(ls "$svc"/dftracer_server_*.pid 2>/dev/null | wc -l)" -ge "$N" ] && break
    sleep 2
  done
  local npid; npid=$(ls "$svc"/dftracer_server_*.pid 2>/dev/null | wc -l)
  echo "service pids $npid/$N" >> "$log"

  cat > "$wrap" <<EOW
#!/bin/bash
cd "$rundir"
exec "$BINARY" $args
EOW
  chmod +x "$wrap"

  if [ "$GENESIS_SCHED" = slurm ]; then
    # --gpus-per-task gives each rank ONE GPU (renumbered 0); --mem=0 because
    # CR_CORE_MEMORY sites cap a few-core rank at a few GB otherwise.
    local gpuarg=""; [ "$GPUS_PER_RANK" -gt 0 ] && gpuarg="--gpus-per-task=$GPUS_PER_RANK"
    # GENESIS_KILL_ON_BAD_EXIT=0 for apps whose ranks legitimately exit non-zero
    # (an input with no reference checksum): with 1, Slurm SIGTERMs the ranks
    # still flushing their traces and the run reports rc=143. APP_TIMEOUT still
    # bounds a real hang.
    timeout "$APP_TIMEOUT" srun --jobid="$SLURM_JOB_ID" -N"$N" -n"$ntasks" \
        --ntasks-per-node="$P" --nodelist="$hostcsv" --overlap --quiet $gpuarg \
        -c "$CORES_PER_RANK" --mem=0 --kill-on-bad-exit="${GENESIS_KILL_ON_BAD_EXIT:-1}" "$wrap" >> "$log" 2>&1
  else
    timeout "$APP_TIMEOUT" flux run -N"$N" -n"$ntasks" -g "$GPUS_PER_RANK" -c "$CORES_PER_RANK" \
        $req --setattr=exclusive=false "$wrap" >> "$log" 2>&1
  fi
  local rc=$?
  local app_rc=$rc
  echo "app_exit=$rc" >> "$log"
  # Some apps exit non-zero on a HEALTHY run (XSBench returns 1 for any input
  # without a reference checksum). GENESIS_OK_RCS lists exit codes accepted as
  # success -- only together with the completion marker below. app_rc keeps the
  # real value for the run JSON.
  case " ${GENESIS_OK_RCS:-0} " in *" $rc "*) rc=0 ;; esac
  if [ -n "$APP_MARKER" ] && ! grep -q "$APP_MARKER" "$log"; then
    echo "INCOMPLETE: no '$APP_MARKER' marker in app output" >> "$log"; [ "$rc" -eq 0 ] && rc=86
  fi

  if [ "$GENESIS_SCHED" = slurm ]; then
    touch "$flag"; wait "$svc_pid" 2>/dev/null; rm -f "$flag"
  else
    DFTRACER_ENABLE=1 DFTRACER_LOG_FILE="$raw/service" \
      flux submit -N"$N" -n"$N" -c1 $req --setattr=exclusive=false \
        "$DFT_BIN" stop "$svc" >> "$log" 2>&1
    sleep 8
  fi
  # Never hand a half-flushed trace to the validator: retest only the files that
  # fail gzip -t, bounded by wall clock (a permanently truncated file must not
  # turn this into an hours-long loop).
  local _bad _until=$(( $(date +%s) + 180 ))
  _bad=$(find "$raw" -maxdepth 1 -name '*.pfw.gz' 2>/dev/null)
  while [ -n "$_bad" ] && [ "$(date +%s)" -lt "$_until" ]; do
    _bad=$(for f in $_bad; do gzip -t "$f" 2>/dev/null || echo "$f"; done)
    [ -n "$_bad" ] && sleep 2
  done
  [ -n "$_bad" ] && echo "TRUNCATED after 180s: $_bad" >> "$log"

  # ---- VALIDATE the artifacts; never trust the exit code alone ----
  # Full content validation: rank set, per-rank args from the SH metadata,
  # trailing `end` event (completeness), required event categories, exact PAPI
  # counters with multiplex==0, one service trace per host with utilization
  # categories and variorum power. A run is checkpointed ONLY if this passes.
  local dt=$(( $(date +%s) - t0 ))
  local vjson="$leaf/raw/validation_${setname}.json"
  GENESIS_EXPECT_ARGS="$args" "$PY" "$SCRIPTS/genesis_validate.py" \
      "$raw" "$nx" "$N" "$P" "$events" > "$vjson" 2>>"$log"
  local vrc=$?
  cat "$vjson" >> "$log"
  rm -f "$wrap"

  # ---- ALWAYS describe the run in JSON, pass or fail ----
  # Written beside the raw traces so it travels with raw/ when genesis_publish.sh
  # moves it to the archive: the archived leaf explains itself even if
  # compaction there never runs.
  local status=ok; { [ "$rc" -eq 0 ] && [ "$vrc" -eq 0 ]; } || status=fail
  "$PY" -c '
import json, sys
a = sys.argv[1:]
rec = ({"key": a[1], "system": a[2], "case": a[3], "nodes": int(a[4]),
           "ppn": int(a[5]), "ntasks": int(a[6]), "papi_set": a[7],
           "papi_counters": a[8].split(","), "hosts": a[9].split(","),
           "binary": a[10], "start_epoch": int(a[11]), "duration_s": int(a[12]),
           "args": a[16], "scheduler": a[17], "gpu_tracing": a[18], "power": a[19],
           "app_exit": int(a[13]), "validate_exit": int(a[14]), "status": a[15],
           "validation": "validation_%s.json" % a[7], "traces": "papi_%s/" % a[7]})
try:   # carry the leaf-contract fields (multiplex, app_files) from validation
    v = json.load(open(a[20]))
    rec.update({k: v.get(k) for k in ("multiplex", "app_files", "ranks", "service_files", "errors")})
except Exception as ex:
    rec["validation_error"] = str(ex)
json.dump(rec, open(a[0], "w"), indent=1)' \
    "$leaf/raw/run_${setname}.json" "$key" "$SYSTEM" "$nx" "$N" "$P" "$ntasks" \
    "$setname" "$events" "$hostcsv" "$BINARY" "$t0" "$dt" "$app_rc" "$vrc" "$status" \
    "$args" "$GENESIS_SCHED" "$GENESIS_GPU" "$GENESIS_POWER" "$vjson" 2>>"$log"

  if [ "$rc" -eq 0 ] && [ "$vrc" -eq 0 ]; then
    echo "$key ok dt=$dt" >> "$STATE"
    echo "OK   $key  (${dt}s)"
  else
    local why; why=$("$PY" -c "import json,sys;print('; '.join(json.load(open(sys.argv[1]))['errors'])[:300])" "$vjson" 2>/dev/null)
    echo "$key FAIL rc=$rc vrc=$vrc dt=$dt :: $why" >> "$LOGDIR/failures"
    echo "FAIL $key  rc=$rc :: $why"
  fi
  echo "$dt" > "$LOGDIR/.dt_$key"
}

# ------------------------------------------------------------- scheduling ----
# Continuous bin-packing scheduler.
#
# The earlier version ran one node-scale at a time and put a BARRIER at the end
# of every batch, so a batch of eight 1-node runs all waited on whichever one
# happened to be nx=200. Here there are no phases and no barriers: all pending
# runs sit in one queue sorted by node count descending (first-fit decreasing),
# and the instant any run finishes and returns its hosts to the free pool, the
# largest still-pending run that fits is dispatched onto them. A 4-node run can
# therefore be in flight alongside two 2-node runs, or eight 1-node runs, and
# the 8 nodes stay busy until the queue drains.
#
# Host slices handed out are always DISJOINT, so concurrently running configs
# never share a node -- essential here, because two jobs on one node would
# contaminate each other's PAPI counters and the node-level power/utilization
# that dftracer_service samples per host.
have_time() {   # $1 = nodes needed
  [ "$DEADLINE" -eq 0 ] && return 0
  local now; now=$(date +%s)
  [ $(( DEADLINE - now )) -gt $(( MAXOBS * 2 + 120 )) ]
}

# One queue of every pending run, biggest node-count first.
mapfile -t WORK < <(
  for N in "${NODE_SCALES[@]}"; do
    [ "$N" -gt "$NALLOC" ] && continue
    for nx in "${INPUTS[@]}"; do
      for P in "${PPNS[@]}"; do
        for entry in "${PAPI_SETS[@]}"; do
          setname="${entry%%:*}"
          key=$(run_key "$nx" "$N" "$P" "$setname")
          grep -q "^$key " "$STATE" 2>/dev/null && continue
          echo "$N $nx $P $setname ${entry#*:}"
        done
      done
    done
  done | sort -s -k1,1nr   # stable: keep the inputs-file order (list inputs fastest-first)
)
TOTAL=$(( ${#INPUTS[@]} * ${#NODE_SCALES[@]} * ${#PPNS[@]} * ${#PAPI_SETS[@]} ))
echo "=== ${#WORK[@]} runs pending of $TOTAL; pool = $NALLOC nodes ==="
[ "${#WORK[@]}" -eq 0 ] && { echo "nothing to do"; exit 0; }

FREE=( "${HOSTS_ALL[@]}" )
declare -A PID_HOSTS
declare -A DISPATCHED

reap() {   # release hosts of any child that has exited
  local p hs
  for p in "${!PID_HOSTS[@]}"; do
    if ! kill -0 "$p" 2>/dev/null; then
      wait "$p" 2>/dev/null
      IFS=',' read -r -a hs <<< "${PID_HOSTS[$p]}"
      FREE+=( "${hs[@]}" )
      unset 'PID_HOSTS[$p]'
    fi
  done
}

deadline_hit=0
while true; do
  reap

  # Dispatch every pending run that fits in the current free pool.
  progressed=1
  while [ "$progressed" -eq 1 ]; do
    progressed=0
    for i in "${!WORK[@]}"; do
      [ -n "${DISPATCHED[$i]:-}" ] && continue
      read -r n nx pp sname evs <<< "${WORK[$i]}"
      [ "$n" -gt "${#FREE[@]}" ] && continue
      if ! have_time "$n"; then deadline_hit=1; break; fi
      slice=$(IFS=,; echo "${FREE[*]:0:$n}")
      FREE=( "${FREE[@]:$n}" )
      run_one "$nx" "$n" "$pp" "$sname" "$evs" "$slice" &
      PID_HOSTS[$!]="$slice"
      DISPATCHED[$i]=1
      progressed=1
    done
    [ "$deadline_hit" -eq 1 ] && break
  done

  # Done when the queue is drained and nothing is still in flight.
  if [ "${#DISPATCHED[@]}" -ge "${#WORK[@]}" ] && [ "${#PID_HOSTS[@]}" -eq 0 ]; then break; fi
  if [ "$deadline_hit" -eq 1 ] && [ "${#PID_HOSTS[@]}" -eq 0 ]; then
    echo "### deadline guard: $(( ${#WORK[@]} - ${#DISPATCHED[@]} )) runs left for the next allocation"
    exit 7
  fi

  # Block until SOMETHING finishes, then loop round and refill its nodes.
  if [ "${#PID_HOSTS[@]}" -gt 0 ]; then
    wait -n 2>/dev/null || true
    reap
    for f in "$LOGDIR"/.dt_*; do
      [ -f "$f" ] || continue
      d=$(cat "$f" 2>/dev/null || echo 0)
      [ "$d" -gt "$MAXOBS" ] 2>/dev/null && MAXOBS=$d
    done
    echo "--- progress: $(wc -l < "$STATE")/$TOTAL  inflight=${#PID_HOSTS[@]} free=${#FREE[@]} (MAXOBS=${MAXOBS}s) ---"
  else
    sleep 2
  fi
done

echo "=== worker finished: $(wc -l < "$STATE")/$TOTAL complete ==="
exit 0
