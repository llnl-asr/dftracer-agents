#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e

N_NODES=4
RANKS_PER_NODE=4
N_RANKS=$((N_NODES * RANKS_PER_NODE))

WS="${WS}"
RUN_NAME=comm_v1_wcbprobe

# Source env inside the allocation
source "$WS/scripts/env.sh"

# Dynamically resolve MASTER_ADDR from allocation's first node (NO nested proxy!)
# MASTER_ADDR must be the node that hosts RANK 0. `flux run -N1 -n1 hostname`
# returns an ARBITRARY node, so rank 0 could not bind and every rank hung in
# init_process_group. Take the FIRST node of the allocation instead, which is
# where flux places task rank 0.
MASTER_ADDR=$(flux run -N"$N_NODES" -n"$N_NODES" hostname 2>/dev/null | head -1)
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
export DFTRACER_LOG_FILE="$WS/comm_v1_wcbprobe/traces/raw/${RUN_NAME}"

# Data paths (real /p/lustre5 paths)
export VOCAB_FILE="$LUSTRE_ROOT/workspaces/megatron-deepspeed-bert/bert-large-uncased-vocab.txt"
export DATASET_PATH="$LUSTRE_ROOT/workspaces/megatron-deepspeed-bert/bert_smoke/wikitext2_smoke_4000doc_text_sentence"

# DeepSpeed config
export DS_CONFIG="$WS/scripts/ds_config_bert_wcb.json"

export CUDA_DEVICE_MAX_CONNECTIONS=1
export PYTORCH_ROCM_ARCH=gfx942
export ROCM_HOME=/opt/rocm-6.4.3
export ROCM_PATH=/opt/rocm-6.4.3

mkdir -p "$WS/comm_v1_wcbprobe/traces/raw"

cd "$WS/annotated/source"

echo "Running BERT baseline with $N_RANKS ranks across $N_NODES nodes"
echo "MASTER_ADDR=$MASTER_ADDR"
echo "DFTracer trace output: $DFTRACER_LOG_FILE"

# Launch with MPI using flux run (we're already inside the proxy)
flux run -N "$N_NODES" -n "$N_RANKS" --exclusive -g1 \
  bash -c 'export RANK=$(($FLUX_TASK_RANK)); export WORLD_SIZE='$N_RANKS'; export LOCAL_RANK=0; export MASTER_ADDR='$MASTER_ADDR'; export MASTER_PORT=29903; python3 pretrain_bert.py \
  --num-layers 12 \
  --hidden-size 768 \
  --num-attention-heads 12 \
  --seq-length 512 \
  --max-position-embeddings 512 \
  --micro-batch-size 4 \
  --global-batch-size 64 \
  --train-iters 400 \
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
  --log-interval 25 \
  --save-interval 1000 \
  --eval-iters 1 \
  --eval-interval 1000 \
  --no-load-optim \
  --no-load-rng \
  --no-pipeline-parallel \
  --deepspeed \
  --deepspeed_config '$DS_CONFIG' \
  --zero-stage 0'

echo "=== BERT Baseline Training Completed at $(date) ==="
