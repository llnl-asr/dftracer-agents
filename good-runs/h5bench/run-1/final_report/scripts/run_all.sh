#!/bin/bash
# Run every case in the optimization ladder, in order.
# Pass the active Flux allocation id as $1 (see `flux jobs`).
#
# NOTE: a bare `flux run` queues a NEW job instead of using your
# allocation -- always go through `flux proxy <alloc>`.
set -e
ALLOC="${1:-}"
if [ -z "$ALLOC" ]; then echo "usage: $0 <flux_alloc_id>"; exit 1; fi
HERE="$(cd "$(dirname "$0")" && pwd)"
WS="${WS:-$(cd "$HERE/../.." && pwd)}"
OBJ="$WS/annotated/source/object"

echo "=== baseline ==="
flux proxy "$ALLOC" flux run -N8 -n384 --exclusive bash "$HERE/run_baseline.sh"

