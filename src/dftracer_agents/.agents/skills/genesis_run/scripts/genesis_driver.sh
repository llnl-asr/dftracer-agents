#!/bin/bash
# Genesis sweep DRIVER -- chains pdebug allocations until the whole
# 7 inputs x 4 node-scales x 3 ppn x 8 PAPI-sets = 672-run matrix is complete.
#
# pdebug caps at 16 nodes and 1 hour per allocation, so the sweep cannot run in
# one shot. The worker checkpoints every VERIFIED run into $STATE and exits
# cleanly (7) when the allocation is about to expire; this driver then asks for
# a fresh allocation and the worker resumes from the checkpoint.
set -uo pipefail

WS=${GENESIS_WS:?set GENESIS_WS to the session workspace}
# THIS system's config, never the session's (see genesis_run.sh).
GENESIS_G="${GENESIS_G:?set GENESIS_G to this genesis system folder}"
source "$GENESIS_G/scripts/genesis_config.sh"
LOGDIR="$WS/artifacts/genesis"
mkdir -p "$LOGDIR"
touch "$STATE"

TOTAL=$(( ${#INPUTS[@]} * ${#NODE_SCALES[@]} * ${#PPNS[@]} * ${#PAPI_SETS[@]} ))
ALLOC_N=${GENESIS_ALLOC_N:-8}       # >= the largest node-scale in the sweep
QUEUE=${GENESIS_QUEUE:?set GENESIS_QUEUE to a queue whose scheduling is STARTED}
ALLOC_T=${GENESIS_ALLOC_T:-58m}     # keep under the queue TLIMIT (system skill)
MAX_ALLOCS=${GENESIS_MAX_ALLOCS:-40}

echo "=== genesis driver: target $TOTAL runs, $(wc -l < "$STATE") already done ==="

for a in $(seq 1 "$MAX_ALLOCS"); do
  done_n=$(wc -l < "$STATE")
  if [ "$done_n" -ge "$TOTAL" ]; then
    echo "=== ALL $TOTAL RUNS COMPLETE ==="; break
  fi
  echo ""
  # --- adaptive allocation sizing -------------------------------------------
  # NEVER idle in the queue waiting for the largest node-scale. Asking for N
  # nodes when fewer are free just parks behind every other job while free
  # nodes sit unused. Size each allocation to what is FREE right now: the worker
  # skips any node-scale larger than its pool, so a small allocation simply
  # drains the small-N work, and a later, larger one picks up the rest. The
  # checkpoint makes that ordering irrelevant to the final corpus.
  free_n=$(flux resource list -o "{state} {queue} {nnodes}" 2>/dev/null \
           | awk -v q="$QUEUE" '$1=="free" && $2 ~ q {s+=$3} END{print s+0}')
  # What node-scales are still pending? Allocating fewer nodes than the smallest
  # one still outstanding accomplishes nothing and burns an allocation, so in
  # that case WAIT for capacity instead of grabbing a useless pool. Conversely
  # never ask for more than the largest scale still pending.
  min_pending=""; max_pending=0
  for _n in "${NODE_SCALES[@]}"; do
    _left=0
    for _nx in "${INPUTS[@]}"; do for _p in "${PPNS[@]}"; do for _e in "${PAPI_SETS[@]}"; do
      grep -q "^nx${_nx}_N${_n}_ppn${_p}_${_e%%:*} " "$STATE" 2>/dev/null || _left=1
    done; done; done
    if [ "$_left" -eq 1 ]; then
      [ -z "$min_pending" ] && min_pending=$_n
      [ "$_n" -lt "$min_pending" ] && min_pending=$_n
      [ "$_n" -gt "$max_pending" ] && max_pending=$_n
    fi
  done
  [ -z "$min_pending" ] && { echo "=== nothing pending ==="; break; }

  want_n=$ALLOC_N
  [ "$max_pending" -gt 0 ] && [ "$max_pending" -lt "$want_n" ] && want_n=$max_pending
  if [ "$free_n" -gt 0 ] && [ "$free_n" -lt "$want_n" ]; then want_n=$free_n; fi
  # QUEUE INSTEAD OF POLL when nothing that fits can make progress.
  #
  # Polling for free nodes is only worthwhile while there is SMALLER work that a
  # smaller allocation could be doing right now. Once every remaining run needs
  # at least `min_pending` nodes and fewer than that are free, waiting achieves
  # nothing that queueing would not -- and queueing is strictly better, because
  # it puts us in line rather than repeatedly checking and losing our place.
  # So: fall through and SUBMIT for min_pending, letting flux schedule it when
  # capacity appears.
  if [ "$want_n" -lt "$min_pending" ]; then
    want_n=$min_pending
    echo "### only $free_n node(s) free and every remaining run needs >= $min_pending"
    echo "### -> submitting -N$want_n and QUEUEING for it rather than polling"
  fi
  if [ "$want_n" -lt 1 ]; then want_n=1; fi
  echo "############ allocation $a/$MAX_ALLOCS  ($done_n/$TOTAL done)  free=$free_n using -N$want_n  $(date) ############"

  flux alloc -q "$QUEUE" -N"$want_n" -t "$ALLOC_T" --job-name=genesis \
      --env=GENESIS_WS --env=GENESIS_SYSTEM --env=GENESIS_G --env=GENESIS_BINARY \
      --env=GENESIS_INPUTS --env=GENESIS_NODE_SCALES --env=GENESIS_PPNS \
      --env=GENESIS_AGG_FILE --env=GENESIS_CORES_PER_RANK --env=GENESIS_ALLOC_SECONDS \
      "$GENESIS_G/scripts/genesis_run.sh" 2>&1 | tee "$LOGDIR/alloc_${a}.log"
  rc=${PIPESTATUS[0]}
  echo "### allocation $a returned rc=$rc, $(wc -l < "$STATE")/$TOTAL done"

  # rc 7 = worker hit the deadline guard on purpose; anything else that made no
  # progress at all means retrying identically will just loop, so back off.
  new_n=$(wc -l < "$STATE")
  if [ "$new_n" -le "$done_n" ] && [ "$rc" -ne 7 ]; then
    echo "### no progress in allocation $a (rc=$rc, pool -N$want_n) -- backing off 120s"
    sleep 120
  fi
done

echo ""
echo "=== driver finished: $(wc -l < "$STATE")/$TOTAL complete ==="
[ -s "$LOGDIR/failures" ] && { echo "--- failures ---"; cat "$LOGDIR/failures"; }
exit 0
