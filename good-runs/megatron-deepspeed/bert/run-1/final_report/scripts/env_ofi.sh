#!/bin/bash
# Canonical env for this session — install AND run source this SAME file.
# Known-good stack reused from prior GPT session (20260726_185517), adapted
# to this session's WS. rocm + rccl modules are load-bearing (see gate 3).
# NEVER pipe `module load` — the pipeline runs Lmod in a SUBSHELL and every setenv it does is
# discarded. That silently dropped NCCL_SOCKET_IFNAME=hsi and FI_MR_CACHE_MONITOR=userfaultfd
# from rccl/working-env for every run before this fix.
# rocm/6.4.3leakfix = 6.4.3 + HSA_USE_UDMABUF=1 (7.2.0) + memory-leak patch (7.14).
# The bare rocm/6.4.3 is the lmod DEFAULT and is unpatched.
module load craype-x86-trento libfabric/match_SHS craype-network-ofi perftools-base/25.09.0 craype/2.7.35 PrgEnv-cray/8.7.0 flux_wrappers/0.1 xpmem/2.6.5 cce/20.0.0 cray-libsci/25.09.0 cray-mpich/9.0.1 rocm/6.4.3leakfix rccl/working-env python/3.13.2 > /tmp/modload.$$.log 2>&1
: "${ROCM_PATH:?rocm module failed to load}"

# CORRECTED 2026-07-28 (see REPORT.md Corrected Findings): this plugin is NOT ABI-incompatible.
# It registers as "AWS Libfabric"; setting NCCL_NET=libfabric explicitly never matches that name
# and forces a hard failure, which was originally misread as an ABI mismatch. Leave NCCL_NET unset
# and put the plugin lib dir on LD_LIBRARY_PATH instead (see env_ofi.sh).

export LD_LIBRARY_PATH="${ROCM_PATH}/lib:/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:${LD_LIBRARY_PATH}"
export LD_LIBRARY_PATH="$CRAY_LD_LIBRARY_PATH:$LD_LIBRARY_PATH"
export LD_LIBRARY_PATH="$WS/install/mpi_shim:/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/lib:/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/lib-abi-mpich:${LD_LIBRARY_PATH}"
export MPI4PY_MPIABI=mpich
export CC=/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicc
export CXX=/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicxx
export ROCM_HOME="${ROCM_PATH}"   # derive from the module; do NOT hardcode /opt/rocm-*
export PATH="${ROCM_PATH}/bin:${PATH}"
export PYTORCH_ROCM_ARCH=gfx942
export CUDA_DEVICE_MAX_CONNECTIONS=1

source "$WS/install/bin/activate"
export LD_LIBRARY_PATH="$WS/install/lib/python3.13/site-packages/torch/lib:${LD_LIBRARY_PATH}"

# nltk punkt tokenizer data, needed by tools/preprocess_data.py --split-sentences
# (used for BERT dataset tokenization; installed into the session venv's own data dir)
export LD_LIBRARY_PATH="/collab/usr/global/tools/rccl/toss_4_x86_64_ib_cray/rocm-6.4.1/install/lib:${LD_LIBRARY_PATH}"
# ^ aws-ofi-rccl plugin: registers as "AWS Libfabric" and is auto-detected.
# Do NOT set NCCL_NET=libfabric -- that name does not match and forces a hard failure.
export NLTK_DATA="$WS/install/nltk_data"
