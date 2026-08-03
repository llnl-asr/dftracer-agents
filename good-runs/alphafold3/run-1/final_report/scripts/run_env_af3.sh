#!/bin/bash
# Sourced by every run_* script below. Provides module loads, venv
# activation, and XLA_FLAGS. Assumes lib_load_config.sh has already been
# sourced (so $WS is set).
module load python/3.11.5
module load rocm/6.0.0
source "${WS}/baseline/af3env/bin/activate"

# Required or dftracer's C extension silently falls back to a NoOpProfiler
# (zero trace events, zero error) -- see REPORT.md Section 9 (lesson).
export LD_LIBRARY_PATH="/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/opt/cray/pe/cce/20.0.0/cce-clang/x86_64/lib:/usr/lib64:${LD_LIBRARY_PATH}"

export XLA_FLAGS="--xla_gpu_enable_triton_softmax_fusion=true --xla_gpu_triton_gemm_any=True"
export AFPY="${WS}/annotated/source/run_alphafold.py"
export PYTHONUNBUFFERED=1

echo "AF3 environment loaded: WS=${WS} python=$(python --version 2>&1)"
