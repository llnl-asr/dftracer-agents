#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e

ALLOC_ID="<flux-jobid>"
N_NODES=4
RANKS_PER_NODE=4
N_RANKS=$((N_NODES * RANKS_PER_NODE))

WS="${WS}"
RUN_NAME="baseline_4n16r"

# Source env inside the allocation
source "$WS/scripts/env.sh"

# Dynamically resolve MASTER_ADDR from allocation's first node
MASTER_ADDR=$(flux proxy "$ALLOC_ID" flux run -N1 -n1 hostname 2>/dev/null | head -1)
if [ -z "$MASTER_ADDR" ]; then
  MASTER_ADDR="localhost"
  echo "WARNING: Could not resolve MASTER_ADDR, using localhost"
fi

echo "MASTER_ADDR resolved to: $MASTER_ADDR"

# DFTracer setup
export DFTRACER_ENABLE=1
export DFTRACER_INIT=FUNCTION
export DFTRACER_INC_METADATA=1
export DFTRACER_DATA_DIR=all
export DFTRACER_LOG_FILE="$WS/baseline/traces/raw/${RUN_NAME}"

# Data paths
export VOCAB_FILE="$LUSTRE_ROOT/workspaces/megatron-deepspeed-bert/bert-large-uncased-vocab.txt"
export DATASET_PATH="$LUSTRE_ROOT/workspaces/megatron-deepspeed-bert/bert_smoke/wikitext2_smoke_4000doc_text_sentence"

# DeepSpeed config
export DS_CONFIG="$WS/scripts/ds_config_bert_baseline.json"

export CUDA_DEVICE_MAX_CONNECTIONS=1
export PYTORCH_ROCM_ARCH=gfx942
export ROCM_HOME=/opt/rocm-6.4.3
export ROCM_PATH=/opt/rocm-6.4.3

mkdir -p "$WS/baseline/traces/raw"

cd "$WS/annotated/source"

echo "Running BERT baseline with $N_RANKS ranks across $N_NODES nodes"
echo "MASTER_ADDR=$MASTER_ADDR"
echo "DFTracer trace output: $DFTRACER_LOG_FILE"

# Launch with MPI using flux run, setting MPI env vars properly
flux run -N "$N_NODES" -n "$N_RANKS" --exclusive \
  -x DFTRACER_ENABLE \
  -x DFTRACER_INIT \
  -x DFTRACER_INC_METADATA \
  -x DFTRACER_DATA_DIR \
  -x DFTRACER_LOG_FILE \
  -x CUDA_DEVICE_MAX_CONNECTIONS \
  -x PYTORCH_ROCM_ARCH \
  -x ROCM_HOME \
  -x ROCM_PATH \
  -x LD_LIBRARY_PATH \
  -x PATH \
  bash -c 'export RANK=$(($FLUX_TASK_RANK)); export WORLD_SIZE='$N_RANKS'; export LOCAL_RANK=$((RANK % '$RANKS_PER_NODE')); export MASTER_ADDR='$MASTER_ADDR'; export MASTER_PORT=29581; python3 pretrain_bert.py \
  --num-layers 12 \
  --hidden-size 768 \
  --num-attention-heads 12 \
  --seq-length 512 \
  --max-position-embeddings 512 \
  --micro-batch-size 4 \
  --global-batch-size 64 \
  --train-iters 10 \
  --lr 1.0e-4 \
  --min-lr 1.0e-6 \
  --init-method-std 0.02 \
  --data-path '$DATASET_PATH' \
  --vocab-file '$VOCAB_FILE' \
  --tokenizer-type BertWordPieceLowerCase \
  --split 949,50,1 \
  --distributed-backend nccl \
  --dataloader-type single \
  --fp16 \
  --num-workers 0 \
  --log-interval 1 \
  --save-interval 1000 \
  --eval-iters 1 \
  --eval-interval 1000 \
  --no-load-optim \
  --no-load-rng \
  --no-pipeline-parallel \
  --deepspeed \
  --deepspeed_config '$DS_CONFIG' \
  --zero-stage 0' 2>&1 | tee "$WS/artifacts/baseline_${RUN_NAME}_run.log"

echo "=== BERT Baseline Completed at $(date) ==="
