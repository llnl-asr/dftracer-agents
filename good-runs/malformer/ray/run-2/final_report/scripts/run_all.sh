#!/bin/bash
# Run every case in the optimization ladder, in order.
# Pass the active Flux allocation id as $1 (see `flux jobs`).
#
# NOTE: a bare `flux run` queues a NEW job instead of using your
# allocation -- always go through `flux proxy <alloc>`.
set -e
ALLOC="${1:-<flux-jobid>}"
if [ -z "$ALLOC" ]; then echo "usage: $0 <flux_alloc_id>"; exit 1; fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"
OBJ="$WS/annotated/source/object"

bash "$HERE/install.sh"

echo "=== 1/5: baseline (2-node) ==="
flux proxy "$ALLOC" flux submit -N2 -n2 -c1 -o spindle.level=off "$HERE/baseline_runner.sh"

echo "=== 2/5: opt1 (bf16, 2-node) ==="
flux proxy "$ALLOC" flux submit -N2 -n2 -c1 -o spindle.level=off "$HERE/opt1_runner.sh"

echo "=== 3/5: opt1_4node (bf16 scaled to 4-node) ==="
flux proxy "$ALLOC" flux submit -N4 -n4 -c1 -o spindle.level=off "$HERE/opt1_4node_runner.sh"

echo "=== 4/5: baseline_4node (fresh, this session's headline run) ==="
flux proxy "$ALLOC" flux submit -N4 -n4 -c1 -o spindle.level=off "$HERE/baseline_4node_runner.sh"

echo "=== 5/5: opt4 + opt5 (compute L2 variants vs baseline_4node) ==="
flux proxy "$ALLOC" flux submit -N4 -n4 -c1 -o spindle.level=off "$HERE/opt4_runner.sh"
flux proxy "$ALLOC" flux submit -N4 -n4 -c1 -o spindle.level=off "$HERE/opt5_runner.sh"

echo "=== done. Compare Job Time / comm share / POSIX I/O against REPORT.md sections 5, 7, 7b. ==="
