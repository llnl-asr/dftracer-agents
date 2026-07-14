#!/bin/bash
# Run the 8-node validation ladder, in order: baseline, then the OMP
# threading fix (opt1). Pass the active Flux allocation id as $1.
#
# NOTE: a bare `flux run` queues a NEW job instead of using your
# allocation -- always go through `flux proxy <alloc>`.
set -e
ALLOC="${1:?usage: $0 <flux_alloc_id>}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "=== baseline (1 thread/rank) ==="
flux proxy "$ALLOC" bash "$HERE/run_baseline_8node.sh" validate8node_benchmark_baseline

echo "=== opt1: OMP threading fix (-c6, OMP_NUM_THREADS=6, spread/cores) ==="
flux proxy "$ALLOC" bash "$HERE/run_opt1_omp_8node.sh" validate8node_benchmark_opt1

echo "Done. Compare *** Done (<seconds>s ...) lines in each run's stdout."
