#!/bin/bash
set -e

# 4-node baseline: GPT-3 Medium 350M config
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$HERE/lib_load_config.sh"
WS="${WS}"

echo "=== Baseline Run Started at $(date) ==="

# Load environment.
# VERIFIED multi-node stack (4-node torch all-reduce passes with exactly this set).
# The `rocm` and `rccl` MODULES are load-bearing: relying on the PyPI torch wheel's
# bundled ROCm alone makes rank 0 die silently inside RCCL's broadcastUniqueNCCLID,
# which surfaces on every other rank as the misleading
#   DistBackendError: ... retrieving ncclUniqueId from [0] ... Failed to recv
# rccl/working-env supplies the two settings RCCL actually needs here
# (FI_MR_CACHE_MONITOR=userfaultfd, NCCL_SOCKET_IFNAME=hsi).
module load craype-x86-trento libfabric/match_SHS craype-network-ofi perftools-base/25.09.0 craype/2.7.35 PrgEnv-cray/8.7.0 flux_wrappers/0.1 xpmem/2.6.5 cce/20.0.0 cray-libsci/25.09.0 cray-mpich/9.0.1 rocm/6.4.3 rccl/working-env python/3.13.2 2>&1 | grep -E "(loading|unloading|Lmod)" | head -5

# Do NOT set NCCL_NET=libfabric / NCCL_NET_PLUGIN here: the site's aws-ofi-rccl
# plugin build (rocm-6.4.1) is not loadable by the RCCL 2.22.3 bundled in this
# torch wheel, and forcing it fails the collective with
#   ncclInvalidUsage ... Error: network libfabric not found.
# Leaving RCCL on its default network selection makes collectives work.

export LD_LIBRARY_PATH="/opt/rocm-6.4.3/lib:/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:${LD_LIBRARY_PATH}"

# CRITICAL: Prepend CRAY_LD_LIBRARY_PATH before Python MPI imports (mpi4py needs libmpi_cray.so)
export LD_LIBRARY_PATH="$CRAY_LD_LIBRARY_PATH:$LD_LIBRARY_PATH"
# mpi4py manylinux wheel dlopens libmpi.so.12 by bare SONAME; cray-mpich ships it as
# libmpi_cray.so.12 under a different dir (lib-abi-mpich) and under a different name.
# The local shim dir provides the libmpi.so.12 -> libmpi_cray.so.12.0.0 symlink; also
# add cray-mpich's own lib and lib-abi-mpich dirs directly since CRAY_LD_LIBRARY_PATH
# alone does not include lib-abi-mpich.
export LD_LIBRARY_PATH="$WS/install/mpi_shim:/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/lib:/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/lib-abi-mpich:$LD_LIBRARY_PATH"
export MPI4PY_MPIABI=mpich
export CC=/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicc
export CXX=/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicxx
export ROCM_HOME=/opt/rocm-6.4.3
export ROCM_PATH=/opt/rocm-6.4.3
# rocminfo must be on PATH: apex/DeepSpeed shell out to it to detect the GPU arch, and
# when it is missing the derived arch string (and therefore the torch cpp_extension
# build hash) changes, which invalidates the JIT cache and makes all 16 ranks recompile
# the same extension concurrently -> they deadlock on the shared build lock.
export PATH="/opt/rocm-6.4.3/bin:${PATH}"
# Pin the arch explicitly so the JIT hash does not depend on rocminfo at all (MI300A).
export PYTORCH_ROCM_ARCH=gfx942
# Megatron's arguments.py hard-requires this for async gradient all-reduce, regardless
# of CUDA vs ROCm backend (it's just an env-var name check, not CUDA-specific behavior).
export CUDA_DEVICE_MAX_CONNECTIONS=1

# Activate venv
source "$WS/install/venv/bin/activate"

# Add torch lib path for dlopen
export LD_LIBRARY_PATH="$WS/install/venv/lib/python3.13/site-packages/torch/lib:${LD_LIBRARY_PATH}"

# DFTracer setup (baseline trace dir)
export DFTRACER_ENABLE=1
export DFTRACER_INIT=FUNCTION
export DFTRACER_INC_METADATA=1
export DFTRACER_DATA_DIR=all
export DFTRACER_LOG_FILE="$WS/baseline/traces/raw/baseline"

# Data path
export DATA_DIR="$WS/dataset/smoke_slice"
export DATASET_PATH="$DATA_DIR/oscar-shuf-eod-gpt2bpe_text_document"

# Distributed rank wiring (torch.distributed env:// convention).
# RANK/WORLD_SIZE/LOCAL_RANK/MASTER_ADDR/MASTER_PORT must be set here, in the same
# script that execs python, since module/env state doesn't persist across separate
# tool calls or shells. TASKS_PER_NODE must match this job's actual slot layout
# (flux resource list showed 4 tasks/node with 1 gpu/task for this allocation).
TASKS_PER_NODE=${TASKS_PER_NODE:-4}
export RANK=0
export WORLD_SIZE=1
# flux run -g1 gives each task exclusive, remapped visibility of exactly ONE GPU
# (always index 0 from that process's view) -- LOCAL_RANK must be 0 here, it is NOT
# an index into a shared per-node device list. Using RANK % TASKS_PER_NODE as the
# cuda/hip device index is wrong and produces "device_id cuda:N is out of range".
export LOCAL_RANK=0
# Resolve MASTER_ADDR once, identically on every task, from the job's own node list.
export MASTER_ADDR="$(flux hostlist local 2>/dev/null | hostlist -n 1 2>/dev/null)"
export MASTER_PORT="${MASTER_PORT:-29570}"

cd "$WS/annotated/source"

echo "Running GPT-3 Medium 350M baseline..."
echo "Dataset: $DATASET_PATH"
echo "Trace output: $DFTRACER_LOG_FILE"
echo "RANK=$RANK WORLD_SIZE=$WORLD_SIZE LOCAL_RANK=$LOCAL_RANK MASTER_ADDR=$MASTER_ADDR MASTER_PORT=$MASTER_PORT"

# GPT-3 Medium 350M config (per pipeline_plan.md Overview)
# 24 layers, 1024 hidden, 16 heads, 256 global batch, 3.0e-4 lr
python3 pretrain_gpt.py \
  --num-layers 24 \
  --hidden-size 1024 \
  --num-attention-heads 16 \
  --seq-length 2048 \
  --max-position-embeddings 2048 \
  --micro-batch-size 4 \
  --global-batch-size 256 \
  --train-iters 3 \
  --lr 3.0e-4 \
  --min-lr 1.0e-6 \
  --init-method-std 0.018 \
  --data-path "$DATASET_PATH" \
  --vocab-file "$DATA_DIR/gpt2-vocab.json" \
  --merge-file "$DATA_DIR/gpt2-merges.txt" \
  --split 1 \
  --distributed-backend gloo \
  --dataloader-type single \
  --fp16 \
  --num-workers 0 \
  --log-interval 1 \
  --save-interval 10 \
  --deepspeed \
  --deepspeed_config "$WS/scripts/ds_config_baseline.json" \
  --zero-stage 0 2>&1

echo "=== Baseline Run Completed at $(date) ==="

