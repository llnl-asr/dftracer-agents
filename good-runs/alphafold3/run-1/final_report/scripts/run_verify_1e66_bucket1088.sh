#!/bin/bash
# DECISIVE 1e66-scale verification: 64-tile-aligned bucket list, replacing
# the app default (which computes 1280 for the 1e66 token count) with 1088.
# CONFIRMED -27% wall time at 1e66 -- see REPORT.md Section 7 row 1 / Section
# 7b Table C. This is the headline result of the session.
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"
source "$HERE/run_env_af3.sh"

RUN_NAME="verify_1e66_bucket1088"
OUTPUT_DIR="${OUTPUT_ROOT}/dataset/${RUN_NAME}"
mkdir -p "${OUTPUT_DIR}" "${OUTPUT_ROOT}/traces/raw/${RUN_NAME}"

export DFTRACER_ENABLE=1
export DFTRACER_LOG_FILE="${OUTPUT_ROOT}/traces/raw/${RUN_NAME}/${RUN_NAME}"
export DFTRACER_DATA_DIR="$VAST_ROOT/smol_workflow/msa_db:${OUTPUT_DIR}"

JSON_PATH="$VAST_ROOT/smol_workflow/pdbbind_casf2016_sample/01_af/1e66.json"

echo "Starting AF3 1e66 VERIFICATION run: bucket list ...,1024,1088,1536,..."
T0=$(date +%s)
python "${AFPY}" \
  --json_path="${JSON_PATH}" \
  --output_dir="${OUTPUT_DIR}" \
  --flash_attention_implementation=xla \
  --buckets=256,512,768,1024,1088,1536,2048,2560,3072,3584,4096,4608,5120
T1=$(date +%s)
echo "AF3 1e66 verification run (bucket<node>) complete. Wall time: $((T1-T0))s (expected ~793s, vs REPORT.md Section 2 baseline range 1077-1097s -- a ~27% reduction)"
