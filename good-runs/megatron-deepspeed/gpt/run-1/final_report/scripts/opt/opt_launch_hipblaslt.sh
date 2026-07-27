#!/bin/bash
# L2 compute lever: force the hipBLASLt GEMM backend for torch on MI300A (gfx942).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$HERE/lib_load_config.sh"
WS="${WS}"
export EXTRA_ENV="TORCH_BLAS_PREFER_HIPBLASLT=1"
exec bash "$WS/scripts/optimizer_compute_launch.sh" "$1" hipblaslt "${2:-20}" "${3:-1}"
