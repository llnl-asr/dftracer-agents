#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$HERE/lib_load_config.sh"
WS="${WS}"
export PRECISION_FLAG="--bf16"
export DS_CONFIG="$WS/scripts/ds_config_bf16.json"
exec bash "$WS/scripts/optimizer_compute_launch.sh" "$1" bf16 "${2:-20}" "${3:-1}"
