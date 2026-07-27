#!/bin/bash
# Launch one compute-optimization variant at the SAME scale as the baseline
# (4 nodes x 4 ranks = 16 ranks, -g1, --exclusive), with dftracer_service
# node-counter bracketing (Pipeline Policy rule 12).
# Usage: optimizer_compute_launch.sh <JOBID> <VARIANT> <TRAIN_ITERS> <REP> [EXTRA_ARGS...]
set -u
JOBID="$1"; VARIANT="$2"; ITERS="$3"; REP="$4"; shift 4
EXTRA="$*"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$HERE/lib_load_config.sh"
WS="${WS}"
LOG="$WS/artifacts/09_optimizer_compute_${VARIANT}_rep${REP}.log"
mkdir -p "$WS/opt_compute/${VARIANT}"

echo "== variant=$VARIANT rep=$REP iters=$ITERS extra=[$EXTRA] jobid=$JOBID =="

#SVC-DISABLED# flux proxy "$JOBID" flux run -N4 -n4 -c1 \
#SVC-DISABLED#   bash -c "source $WS/scripts/env.sh && python3 -m dftracer.service" \
#SVC-DISABLED#   > "$WS/artifacts/09_service_${VARIANT}_rep${REP}.log" 2>&1 &
#SVC-DISABLED# SVC=$!
#SVC-DISABLED# sleep 3

flux proxy "$JOBID" flux run -N4 -n16 -g1 --exclusive \
  env VARIANT="${VARIANT}_rep${REP}" TRAIN_ITERS="$ITERS" EXTRA_ARGS="$EXTRA" ${EXTRA_ENV:-OPT_COMPUTE_PLACEHOLDER=1} PRECISION_FLAG="${PRECISION_FLAG:---fp16}" DS_CONFIG="${DS_CONFIG:-$WS/scripts/ds_config_baseline.json}" \
  bash "$WS/scripts/optimizer_compute_variant.sh" > "$LOG" 2>&1
RC=$?

#SVC-DISABLED# kill $SVC 2>/dev/null || true
#SVC-DISABLED# wait $SVC 2>/dev/null || true

echo "exit=$RC log=$LOG"
echo "---- steady-state steps ----"
grep -ao "steps: [0-9]* loss: [0-9.]* iter time (s): [0-9.]* samples/sec: [0-9.]*" "$LOG" | head -40
echo "---- completion lines (expect 16) ----"
grep -ac "Completed at" "$LOG"
exit $RC
