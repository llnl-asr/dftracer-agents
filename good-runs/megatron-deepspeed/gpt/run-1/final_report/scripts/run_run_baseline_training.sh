#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e

JOBID="f3NZCB1FchcT"
WS="${WS}"

echo "Starting baseline 16-rank training run..."
echo "JOBID: $JOBID"
echo "Workspace: $WS"

# Verify allocation is still running
ALLOC_STATUS=$(flux jobs -no "{state}" "$JOBID" 2>/dev/null || echo "unknown")
echo "Allocation status: $ALLOC_STATUS"

# Run the training via flux proxy
flux proxy $JOBID flux run -N 4 -n 16 -g 1 \
  bash "$WS/scripts/baseline_train_16rank.sh"

echo "Training completed!"
