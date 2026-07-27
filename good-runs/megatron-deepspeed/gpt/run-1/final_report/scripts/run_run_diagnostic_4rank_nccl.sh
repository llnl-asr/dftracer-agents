#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

WS=${WS}
DIAG_LOG="$WS/artifacts/diagnostic_4rank_nccl.log"
echo "=== 4-Rank NCCL Diagnostic Run ===" | tee "$DIAG_LOG"
date >> "$DIAG_LOG"
bash "$WS/scripts/baseline_train_4rank_nccl.sh" 2>&1 | tee -a "$DIAG_LOG"
echo "=== RUN COMPLETE ===" >> "$DIAG_LOG"
date >> "$DIAG_LOG"
