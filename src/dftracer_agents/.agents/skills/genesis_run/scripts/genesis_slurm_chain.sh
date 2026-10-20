#!/bin/bash
# Slurm counterpart of genesis_driver.sh: submit a DEPENDENCY CHAIN of sbatch
# jobs, each running genesis_run.sh (the packing worker) inside its own
# allocation. No login-node process has to stay alive (login sessions get torn
# down and orphaned drivers keep submitting); `afterany` guarantees exactly ONE
# runnable worker per chain, and the worker's flock on its state file enforces
# one-worker-per-checkpoint even if two chains are submitted by mistake.
#
# One chain per DISJOINT slice of work, each with its OWN state file, e.g.
#   GENESIS_NODE_SCALES=1 GENESIS_STATE=$G/artifacts/state_N1 PART=pdebug N=1
#   GENESIS_NODE_SCALES=4 GENESIS_STATE=$G/artifacts/state_N4 PART=pci    N=4
#
# Usage: genesis_slurm_chain.sh <partition> <nodes> <minutes> <depth> <name>
# All GENESIS_* variables in the environment are exported into the jobs.
set -uo pipefail
PART=${1:?partition}; NN=${2:?nodes}; MIN=${3:?minutes}; DEPTH=${4:?depth}; NAME=${5:?name}
: "${GENESIS_G:?set GENESIS_G}" "${GENESIS_STATE:?set GENESIS_STATE (one per chain)}"
LOG="${GENESIS_LOGDIR:-$GENESIS_G/artifacts}"; mkdir -p "$LOG"
EXTRA=${GENESIS_SBATCH_EXTRA:-}          # e.g. "--gres=gpu:4 --exclusive"
prev=""
for i in $(seq 1 "$DEPTH"); do
  dep=""; [ -n "$prev" ] && dep="--dependency=afterany:$prev"
  # %j is Slurm's job-id template (flux uses {{id}} instead).
  id=$(sbatch --parsable -p "$PART" -N"$NN" -t "$MIN" $EXTRA $dep \
         --job-name="genesis_$NAME" --export=ALL \
         --signal=B:TERM@120 \
         -o "$LOG/batch_${NAME}_%j.log" \
         --wrap "$GENESIS_G/scripts/genesis_run.sh") || { echo "sbatch failed"; exit 1; }
  echo "chain $NAME [$i/$DEPTH]: job $id ${dep:+($dep)}"
  echo "$id" >> "$LOG/chain_${NAME}.jobs"
  prev=$id
done
