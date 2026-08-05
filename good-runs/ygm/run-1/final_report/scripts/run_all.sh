#!/bin/bash
# Run every case in the optimization ladder, in order.
# Pass the active Flux allocation id as $1 (see `flux jobs`).
#
# NOTE: a bare `flux run` queues a NEW job instead of using your
# allocation -- always go through `flux proxy <alloc>`.
set -e
ALLOC="${1:-}"
if [ -z "$ALLOC" ]; then echo "usage: $0 <flux_alloc_id>"; exit 1; fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"
OBJ="$WS/annotated/source/object"

echo "=== dftracer_run_ygm_bench ==="
flux proxy "$ALLOC" bash "$HERE/run_dftracer_run_ygm_bench.sh"

echo "=== probe_wrapper ==="
flux proxy "$ALLOC" bash "$HERE/run_probe_wrapper.sh"

echo "=== dftracer_run_ygm_bench_v2 ==="
flux proxy "$ALLOC" bash "$HERE/run_dftracer_run_ygm_bench_v2.sh"

echo "=== dftracer_run_ygm_bench_4n ==="
flux proxy "$ALLOC" bash "$HERE/run_dftracer_run_ygm_bench_4n.sh"

