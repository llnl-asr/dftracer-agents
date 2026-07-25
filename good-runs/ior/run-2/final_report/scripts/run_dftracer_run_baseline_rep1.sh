#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
export DFTRACER_ENABLE=1
export DFTRACER_INC_METADATA=1
export DFTRACER_LOG_FILE=${WS}/baseline_rep1/traces/raw/baseline_rep1-85b75add
export DFTRACER_DATA_DIR=all
cd ${WS}/build_ann
bash ${WS}/baseline/scripts/run.sh
