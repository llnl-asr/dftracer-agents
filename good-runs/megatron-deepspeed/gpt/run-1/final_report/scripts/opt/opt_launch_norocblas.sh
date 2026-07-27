#!/bin/bash
# L2 compute lever: force the legacy rocBLAS GEMM backend (hipBLASLt disabled).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$HERE/lib_load_config.sh"
WS="${WS}"
export EXTRA_ENV="TORCH_BLAS_PREFER_HIPBLASLT=0"
exec bash "$WS/scripts/optimizer_compute_launch.sh" "$1" rocblas "${2:-20}" "${3:-1}"
