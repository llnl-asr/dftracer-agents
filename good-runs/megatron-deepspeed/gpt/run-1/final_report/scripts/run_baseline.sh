#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e

WS=${WS}
source "$WS/scripts/env.sh"
source "$WS/install/venv/bin/activate"

export CC=/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicc
export CXX=/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicxx
export ROCM_HOME=/opt/rocm-6.4.3
export ROCM_PATH=/opt/rocm-6.4.3

export LD_LIBRARY_PATH="$WS/install/venv/lib/python3.13/site-packages/torch/lib:${LD_LIBRARY_PATH}"

export WANDB_MODE=disabled
export DS_BUILD_SHM_COMM=0
export TORCH_EXTENSIONS_DIR="$WS/install/torch_ext_cache"
mkdir -p "$TORCH_EXTENSIONS_DIR"

export DFTRACER_ENABLE=1
export DFTRACER_INIT=FUNCTION
export DFTRACER_INC_METADATA=1
export DFTRACER_DATA_DIR=all
mkdir -p "$WS/baseline/traces/raw"
export DFTRACER_LOG_FILE="$WS/baseline/traces/raw/baseline"

export NCCL_DEBUG=INFO
export NCCL_BLOCKING_WAIT=1
export NCCL_TIMEOUT=3600

DATA_DIR="$WS/dataset/smoke_slice"
OUT_DIR="$DATA_DIR/output_baseline_2node"
mkdir -p "$OUT_DIR"

echo "[$(hostname)] FLUX_TASK_RANK=$FLUX_TASK_RANK: 2-node baseline starting..."

cd "$WS/annotated/source"

torchrun \
  --nnodes=2 \
  --node_rank=$FLUX_TASK_RANK \
  --nproc_per_node=4 \
  --master_addr=tuolumne1158 \
  --master_port=29500 \
  pretrain_gpt.py \
    --num-layers 2 \
    --hidden-size 64 \
    --num-attention-heads 4 \
    --seq-length 64 \
    --micro-batch-size 1 \
    --global-batch-size 2 \
    --train-iters 3 \
    --vocab-file "$DATA_DIR/gpt2-vocab.json" \
    --merge-file "$DATA_DIR/gpt2-merges.txt" \
    --data-path "$DATA_DIR/oscar-shuf-eod-gpt2bpe_text_document" \
    --data-impl mmap \
    --split 949,50,1 \
    --distributed-backend nccl \
    --lr 0.00015 \
    --min-lr 0.00001 \
    --weight-decay 1e-2 \
    --fp16 \
    --num-workers 0 \
    --deepspeed \
    --deepspeed_config "$DATA_DIR/ds_config_smoke.json" \
    --zero-stage 0 \
    --no-gradient-accumulation-fusion

echo "[$(hostname)] 2-node baseline completed"
