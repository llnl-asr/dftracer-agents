#!/bin/bash
# Run every case in the optimization ladder, in order.
# Pass the active Flux allocation id as $1 (see `flux jobs`).
#
# NOTE: a bare `flux run` queues a NEW job instead of using your
# allocation -- always go through `flux proxy <alloc>`.
set -e
ALLOC="${1:-flux-alloc}"
if [ -z "$ALLOC" ]; then echo "usage: $0 <flux_alloc_id>"; exit 1; fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"
OBJ="$WS/annotated/source/object"

echo "=== baseline (smoke-scale, STEP 6/7) ==="
flux proxy "$ALLOC" bash "$HERE/run_baseline.sh"

echo "=== STEP 9 N-node validation: val_baseline (1 node x 4 GPU, real PubChem data) ==="
flux proxy "$ALLOC" bash "$HERE/run_val_baseline.sh"

echo "=== STEP 9 validation: val_opt1_bf16 (bf16-mixed precision -- measured -43%, rejected) ==="
flux proxy "$ALLOC" bash "$HERE/run_val_opt1_bf16.sh"

echo "=== STEP 9 validation: val_opt2_workers1 (num_workers=1 -- measured neutral) ==="
flux proxy "$ALLOC" bash "$HERE/run_val_opt2_workers1.sh"

echo "=== STEP 9 validation: val_opt3_batch64 (batch=64 -- measured +81%) ==="
flux proxy "$ALLOC" bash "$HERE/run_val_opt3_batch64.sh"

echo "=== STEP 9 validation: val_opt4_combined (batch=128 + expandable_segments -- measured +206%, best config) ==="
flux proxy "$ALLOC" bash "$HERE/run_val_opt4_combined.sh"
