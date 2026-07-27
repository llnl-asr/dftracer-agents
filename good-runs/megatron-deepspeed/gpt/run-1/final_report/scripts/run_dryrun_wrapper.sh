#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
JOBID="$1"
WS="${WS}"

echo "Starting 2-node dry run at $(date)"
flux proxy "$JOBID" bash "$WS/scripts/dry_run_2node.sh"
echo "Dry run completed at $(date)"
