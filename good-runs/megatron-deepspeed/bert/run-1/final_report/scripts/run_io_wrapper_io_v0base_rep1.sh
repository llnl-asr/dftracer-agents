#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
WS="${WS}"
VARIANT="v0base"
RUN_NAME="io_v0base_rep1"
N_NODES=4
N_RANKS=16
PORT=39018
source "$WS/scripts/env.sh"
MASTER_ADDR=$(flux run -N"$N_NODES" -n"$N_NODES" hostname 2>/dev/null | head -1)
echo "MASTER_ADDR=$MASTER_ADDR PORT=$PORT VARIANT=$VARIANT"

export DFTRACER_ENABLE=1
export DFTRACER_INIT=FUNCTION
export DFTRACER_INC_METADATA=1
export DFTRACER_DATA_DIR=all
export DFTRACER_LOG_FILE="$WS/opt_io/$RUN_NAME/traces/raw/${RUN_NAME}"

VOCAB_FILE="$LUSTRE_ROOT/workspaces/megatron-deepspeed-bert/bert-large-uncased-vocab.txt"
DATASET_PATH="$LUSTRE_ROOT/workspaces/megatron-deepspeed-bert/bert_smoke/wikitext2_smoke_4000doc_text_sentence"
NUM_WORKERS=0

if [ "$VARIANT" = "v1shm" ]; then
  SRC=$LUSTRE_ROOT/workspaces/megatron-deepspeed-bert
  flux run -N"$N_NODES" -n"$N_NODES" bash -c "mkdir -p /dev/shm/\$USER/mdbert/bert_smoke && cp -f $SRC/bert-large-uncased-vocab.txt /dev/shm/\$USER/mdbert/ && cp -f $SRC/bert_smoke/wikitext2_smoke_4000doc_text_sentence* /dev/shm/\$USER/mdbert/bert_smoke/ && ls /dev/shm/\$USER/mdbert/bert_smoke | wc -l"
  VOCAB_FILE="/dev/shm/$USER/mdbert/bert-large-uncased-vocab.txt"
  DATASET_PATH="/dev/shm/$USER/mdbert/bert_smoke/wikitext2_smoke_4000doc_text_sentence"
fi
if [ "$VARIANT" = "v2nw4" ]; then
  NUM_WORKERS=4
fi

export DS_CONFIG="$WS/scripts/ds_config_bert_baseline.json"
export CUDA_DEVICE_MAX_CONNECTIONS=1
export PYTORCH_ROCM_ARCH=gfx942
export ROCM_HOME=/opt/rocm-6.4.3
export ROCM_PATH=/opt/rocm-6.4.3
export PECAN_TORCH_COMPILE=0

cd "$WS/annotated/source"
flux run -N "$N_NODES" -n "$N_RANKS" --exclusive -g1 \
  bash -c 'export RANK=$(($FLUX_TASK_RANK)); export WORLD_SIZE='$N_RANKS'; export LOCAL_RANK=0; export MASTER_ADDR='$MASTER_ADDR'; export MASTER_PORT='$PORT'; python3 pretrain_bert.py \
  --num-layers 12 --hidden-size 768 --num-attention-heads 12 \
  --seq-length 512 --max-position-embeddings 512 \
  --micro-batch-size 4 --global-batch-size 64 --train-iters 10 \
  --lr 1.0e-4 --min-lr 1.0e-6 --init-method-std 0.02 \
  --data-path '$DATASET_PATH' --vocab-file '$VOCAB_FILE' \
  --tokenizer-type BertWordPieceLowerCase --split 949,50,1 \
  --distributed-backend nccl --dataloader-type single --fp16 \
  --num-workers '$NUM_WORKERS' --log-interval 1 --save-interval 1000 \
  --eval-iters 1 --eval-interval 1000 --no-load-optim --no-load-rng \
  --no-pipeline-parallel --deepspeed --deepspeed_config '$DS_CONFIG' --zero-stage 0'
echo "=== DONE $RUN_NAME at $(date) ==="
