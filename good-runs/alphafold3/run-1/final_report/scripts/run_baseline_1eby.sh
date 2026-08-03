#!/bin/bash
# Baseline run, structure 1eby (18MB MSA). See REPORT.md Section 2.
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"
source "$HERE/run_env_af3.sh"

RUN_NAME="${1:-baseline_1eby}"
export DFTRACER_ENABLE=1
export DFTRACER_LOG_FILE="${OUTPUT_ROOT}/traces/raw/${RUN_NAME}"
export DFTRACER_DATA_DIR="$VAST_ROOT/smol_workflow/msa_db:${OUTPUT_ROOT}/dataset/${RUN_NAME}"
mkdir -p "${OUTPUT_ROOT}/traces/raw" "${OUTPUT_ROOT}/dataset/${RUN_NAME}"

JSON_PATH="$VAST_ROOT/smol_workflow/pdbbind_casf2016_sample/01_af/1eby.json"
OUTPUT_DIR="${OUTPUT_ROOT}/dataset/${RUN_NAME}"

echo "Starting AF3 baseline run: 1eby (expect ~110-115s app total)"
T0=$(date +%s)
python "${AFPY}" --json_path="${JSON_PATH}" --output_dir="${OUTPUT_DIR}" \
  --flash_attention_implementation=xla
T1=$(date +%s)
echo "AF3 baseline run (${RUN_NAME}) complete. Wall time: $((T1-T0))s (compare to REPORT.md Section 2)"
