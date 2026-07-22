#!/bin/bash
# Compute-optimized run: I/O opts above PLUS loss-block host-device sync
# rewrite in pecan/trainer.py (removed 7+ blocking .cpu() calls;
# torch.FloatTensor(...) -> torch.stack(...).view(())).
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"
RUN=opt5_losssync
PORT=29513

source "$WS/scripts_env.sh"
export PECAN_INDEX_CSV_DIR="$OUTPUT_ROOT/$RUN/checkpoint"
export DFTRACER_ENABLE=1
export DFTRACER_DATA_DIR=all
export DFTRACER_INC_METADATA=1
export DFTRACER_TORCH_PROFILE=1
export DFTRACER_LOG_FILE="$OUTPUT_ROOT/$RUN/traces/raw/$RUN"
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True,garbage_collection_threshold:0.8
export PECAN_TORCH_COMPILE=0
export PECAN_PREFETCH_FACTOR=4
mkdir -p "$OUTPUT_ROOT/$RUN/traces/raw" "$OUTPUT_ROOT/$RUN/checkpoint"

NODELIST=$(flux job info "$FLUX_JOB_ID" R 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['execution']['nodelist'][0])")
export MASTER_ADDR=$(flux hostlist -n 0 "$NODELIST")
export MASTER_PORT=$PORT

cd "$WS/annotated/source"
python -u main_app.py --run-mode 1 \
  --config yaml/pecan/config_pecan_pdbspheres_v2_session.yaml \
  --epochs 2 --batch-size 8 --num-workers 2 \
  --checkpoint-dir "$OUTPUT_ROOT/$RUN/checkpoint" --checkpoint-prefix "$RUN"
