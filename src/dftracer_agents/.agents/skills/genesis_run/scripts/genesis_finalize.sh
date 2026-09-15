#!/bin/bash
# Finalize the genesis corpus for THIS system only:
#   1. compact every leaf (raw app + service traces -> verified chunks)
#   2. regenerate the system README with real stats
# Touches only genesis_traces/$SYSTEM -- other systems' trees are never read
# or written, because every path goes through leaf_dir().
set -uo pipefail
WS=${GENESIS_WS:?set GENESIS_WS to the session workspace}
source "$WS/scripts/genesis_config.sh"
echo "=== finalizing system: $SYSTEM ==="
echo "--- 1/2 compaction ---"
"$WS/scripts/genesis_compact.sh"
echo "--- 2/2 README ---"
"$WS/venv/bin/python" "$WS/scripts/genesis_readme.py" "$GENESIS/$SYSTEM" "$WS/artifacts/genesis/meta.json"
echo "=== finalize done ==="
