#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
export DFTRACER_ENABLE=1
export DFTRACER_INC_METADATA=1
export DFTRACER_LOG_FILE=${WS}/opt1/traces/raw/opt1-1deacd8b
export DFTRACER_DATA_DIR=all
export DFTRACER_INIT=FUNCTION
export IND_ROW_PRECOMPUTE=1
export IND_TAR_STREAM=1
export LD_LIBRARY_PATH=/opt/cray/pe/lib64:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce-clang/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64
cd ${WS}/source
flux proxy f3NXj3jCbhtK flux run -N4 -n32 bash ${WS}/opt1/scripts/pmc_wrapper.sh
