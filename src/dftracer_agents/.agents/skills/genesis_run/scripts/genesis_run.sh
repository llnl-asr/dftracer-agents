#!/bin/bash
# Genesis sweep WORKER -- runs INSIDE a flux allocation.
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
source "$WS/scripts/env.sh"           > /dev/null 2>&1
source "$WS/scripts/genesis_config.sh"

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
# If flux will tell us the real expiration of the enclosing allocation, prefer it.
if _exp=$(flux --parent job info "${FLUX_JOB_ID:-}" R 2>/dev/null \
          | python3 -c 'import json,sys; print(int(json.load(sys.stdin)["execution"]["expiration"]))' 2>/dev/null) \
   && [ -n "$_exp" ] && [ "$_exp" -gt 0 ] 2>/dev/null; then
  DEADLINE=$(( _exp - 60 ))
  echo "deadline from flux: $DEADLINE ($(( (DEADLINE - $(date +%s)) / 60 )) min of allocation left)"
else
  echo "deadline from budget: $DEADLINE ($(( ALLOC_SECONDS / 60 )) min budget, flux expiration unavailable)"
fi

# LOGDIR comes from genesis_config.sh ($G/artifacts): per-system, so one
# machine’s run logs never land in another machine’s artifacts directory.
mkdir -p "$LOGDIR" "$GENESIS"
touch "$STATE"

mapfile -t HOSTS_ALL < <(flux hostlist -e local 2>/dev/null | tr ' ' '\n' | grep -v '^$')
NALLOC=${#HOSTS_ALL[@]}
[ "$NALLOC" -eq 0 ] && { echo "FATAL: no hosts in allocation"; exit 1; }
echo "=== genesis worker: $NALLOC nodes: ${HOSTS_ALL[*]} ==="

MAXOBS=90        # observed worst-case run wall time, grows as we measure

# ---------------------------------------------------------------- one run ----
run_one() {
  local nx=$1 N=$2 P=$3 setname=$4 events=$5 hostcsv=$6
  local key; key=$(run_key "$nx" "$N" "$P" "$setname")
  local leaf; leaf=$(leaf_dir "$nx" "$N" "$P")
  local raw="$leaf/raw/papi_${setname}"
  local svc="$raw/dftracer_service"
  local rundir="$WS/dataset/genesis/$key"     # app output -> PFS, never the workspace
  local wrap="$WS/tmp/_genesis_${key}.sh"
  local log="$LOGDIR/${key}.log"
  local ntasks=$(( N * P ))
  local req="--requires=host:$hostcsv"

  rm -rf "$raw"; mkdir -p "$svc" "$rundir"

  export DFTRACER_ENABLE=1
  export DFTRACER_INIT=FUNCTION                 # FUNCTION mode always, never PRELOAD
  export DFTRACER_LOG_FILE="$raw/$key"
  export DFTRACER_DATA_DIR=all
  export DFTRACER_TRACE_COMPRESSION=1
  export DFTRACER_INC_METADATA=1
  export DFTRACER_ENABLE_PAPI_TRACING=1
  export DFTRACER_PAPI_SAMPLE_INTERVAL_MS=100
  export DFTRACER_PAPI_EVENTS="$events"
  export DFTRACER_ENABLE_HIP_TRACING=1          # ROCProfiler: this is a GPU code

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
  unset DFTRACER_DISABLE_VARIORUM_POWER         # variorum power on (service side)

  {
    echo "### $key hosts=$hostcsv ntasks=$ntasks counters=$events"
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
  DFTRACER_ENABLE=1 DFTRACER_LOG_FILE="$raw/service" \
    flux submit -N"$N" -n"$N" -c1 $req --setattr=exclusive=false \
      "$DFT_BIN" start "$svc" >> "$log" 2>&1
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
exec "$BINARY" nx=$nx ny=$nx nz=$nx
EOW
  chmod +x "$wrap"

  flux run -N"$N" -n"$ntasks" -g "$GPUS_PER_RANK" -c "$CORES_PER_RANK" \
      $req --setattr=exclusive=false "$wrap" >> "$log" 2>&1
  local rc=$?
  echo "app_exit=$rc" >> "$log"

  DFTRACER_ENABLE=1 DFTRACER_LOG_FILE="$raw/service" \
    flux submit -N"$N" -n"$N" -c1 $req --setattr=exclusive=false \
      "$DFT_BIN" stop "$svc" >> "$log" 2>&1
  sleep 8

  # ---- VALIDATE the artifacts; never trust the exit code alone ----
  # Full content validation: rank set, per-rank args from the SH metadata,
  # trailing `end` event (completeness), required event categories, exact PAPI
  # counters with multiplex==0, one service trace per host with utilization
  # categories and variorum power. A run is checkpointed ONLY if this passes.
  local dt=$(( $(date +%s) - t0 ))
  local vjson="$leaf/raw/validation_${setname}.json"
  "$WS/venv/bin/python" "$WS/scripts/genesis_validate.py" \
      "$raw" "$nx" "$N" "$P" "$events" > "$vjson" 2>>"$log"
  local vrc=$?
  cat "$vjson" >> "$log"
  rm -f "$wrap"

  if [ "$rc" -eq 0 ] && [ "$vrc" -eq 0 ]; then
    echo "$key ok dt=$dt" >> "$STATE"
    echo "OK   $key  (${dt}s)"
  else
    local why; why=$("$WS/venv/bin/python" -c "import json,sys;print('; '.join(json.load(open(sys.argv[1]))['errors'])[:300])" "$vjson" 2>/dev/null)
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
  done | sort -k1,1nr -k2,2n
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
