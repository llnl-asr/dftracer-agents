#!/bin/bash
# Multi-rank BERT baseline: 4 nodes x 4 ranks/node = 16 ranks
# Fixed version with correct flux proxy usage
set -e

ALLOC_ID="${1:?Usage: $0 <flux-alloc-id>}"
N_NODES=4
RANKS_PER_NODE=4
N_RANKS=$((N_NODES * RANKS_PER_NODE))

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$HERE/lib_load_config.sh"
RUN_NAME=ab_A_29903

echo "=== BERT Baseline (4N x 16R) Started at $(date) ==="

# Verify allocation exists
ALLOC_STATE=$(flux jobs -a --no-header -o "{state}" "$ALLOC_ID" 2>/dev/null)
if [ "$ALLOC_STATE" != "RUN" ]; then
  echo "ERROR: Allocation $ALLOC_ID is not running (state: $ALLOC_STATE)"
  exit 1
fi

echo "Allocation $ALLOC_ID is active, state=$ALLOC_STATE"

# Create the MPI wrapper to run INSIDE the flux proxy
cat > "$WS/tmp/run_bert_mpi_wrapper_v2.sh" << 'ENDWRAPPER'
#!/bin/bash
set -e

N_NODES=4
RANKS_PER_NODE=4
N_RANKS=$((N_NODES * RANKS_PER_NODE))

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$HERE/lib_load_config.sh"
RUN_NAME=ab_A_29903

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
export DFTRACER_LOG_FILE="$WS/ab_A_29903/traces/raw/${RUN_NAME}"

# Data paths (real /p/lustre5 paths)
export VOCAB_FILE="${DATASET_ROOT}/bert-large-uncased-vocab.txt"
export DATASET_PATH="${DATASET_ROOT}/bert_smoke/wikitext2_smoke_4000doc_text_sentence"

# DeepSpeed config
export DS_CONFIG="$WS/scripts/ds_config_bert_baseline.json"

export CUDA_DEVICE_MAX_CONNECTIONS=1
export PYTORCH_ROCM_ARCH=gfx942
export ROCM_HOME=/opt/rocm-6.4.3
export ROCM_PATH=/opt/rocm-6.4.3

mkdir -p "$WS/ab_A_29903/traces/raw"

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
  --log-interval 1 \
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
ENDWRAPPER

chmod +x "$WS/tmp/run_bert_mpi_wrapper_v2.sh"
echo "Created fixed MPI wrapper script"

# Now launch via flux proxy (JUST ONCE)
echo "Launching via flux proxy $ALLOC_ID..."
flux proxy "$ALLOC_ID" bash "$WS/tmp/run_bert_mpi_wrapper_v2.sh" 2>&1 | tee "$WS/artifacts/baseline_${RUN_NAME}_run.log"

echo "=== BERT Baseline Launch Complete ==="
