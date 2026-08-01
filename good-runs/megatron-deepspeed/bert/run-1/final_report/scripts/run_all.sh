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

echo "=== run_bert_mpi_wrapper ==="
flux proxy "$ALLOC" bash "$HERE/run_run_bert_mpi_wrapper.sh"

echo "=== run_bert_mpi_3n12r ==="
flux proxy "$ALLOC" bash "$HERE/run_run_bert_mpi_3n12r.sh"

echo "=== io_wrapper_io_v0base_rep1 ==="
flux proxy "$ALLOC" bash "$HERE/run_io_wrapper_io_v0base_rep1.sh"

echo "=== comm_wrapper_v1 ==="
flux proxy "$ALLOC" bash "$HERE/run_comm_wrapper_v1.sh"

echo "=== 7b_probe ==="
flux proxy "$ALLOC" bash "$HERE/run_7b_probe.sh"

echo "=== comm_wrapper_v2 ==="
flux proxy "$ALLOC" bash "$HERE/run_comm_wrapper_v2.sh"

echo "=== comm_wrapper_v3 ==="
flux proxy "$ALLOC" bash "$HERE/run_comm_wrapper_v3.sh"

echo "=== plugin_probe ==="
flux proxy "$ALLOC" bash "$HERE/run_plugin_probe.sh"

echo "=== run_bert_mpi_wrapper_v2 ==="
flux proxy "$ALLOC" bash "$HERE/run_run_bert_mpi_wrapper_v2.sh"

