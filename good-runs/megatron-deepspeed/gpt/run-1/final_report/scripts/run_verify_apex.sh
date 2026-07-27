#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
WS=${WS}
source "$WS/scripts/env.sh"
source "$WS/install/venv/bin/activate"
export ROCM_HOME=/opt/rocm-6.4.3
export ROCM_PATH=/opt/rocm-6.4.3
export PATH="$ROCM_HOME/bin:$PATH"
python -c "from apex.normalization.fused_layer_norm import FusedLayerNormAffineFunction; print(\"apex ok\")"
