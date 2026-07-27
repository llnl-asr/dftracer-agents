#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

WS=${WS}
DIAGNOSTIC_LOG="$WS/artifacts/diagnostic_ddp_init_hang.log"
bash "$WS/scripts/baseline_train_16rank_fixed.sh" 2>&1 | tee -a "$DIAGNOSTIC_LOG"
