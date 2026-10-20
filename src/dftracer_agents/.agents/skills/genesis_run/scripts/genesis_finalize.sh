#!/bin/bash
# Finalize the genesis corpus for THIS system only:
#   1. publish: MOVE each sealed leaf's raw/ (+ run_*.json) to the archive,
#      then compact it IN PLACE there (COMPACTION_NOT_RUN.md if it fails)
#   2. regenerate the system README with real stats, in the archive
# Touches only this system's tree -- every path goes through leaf_dir().
set -uo pipefail
WS=${GENESIS_WS:?set GENESIS_WS to the session workspace}
GENESIS_G="${GENESIS_G:?set GENESIS_G to this genesis system folder}"
: "${GENESIS_ARCHIVE:?set GENESIS_ARCHIVE to <archive>/<app>/<system>}"
source "$GENESIS_G/scripts/genesis_config.sh"
echo "=== finalizing system: $SYSTEM ==="
echo "--- 1/2 publish (move raw) + compact in place ---"
"$GENESIS_G/scripts/genesis_publish.sh"
echo "--- 2/2 README ---"
"$PY" "$SCRIPTS/genesis_readme.py" "$GENESIS_ARCHIVE" "$WS/artifacts/genesis/meta.json"
echo "=== finalize done ==="
