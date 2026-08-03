#!/bin/bash
# 1e66-scale transfer check for the 1eby memory win
# (XLA_PYTHON_CLIENT_PREALLOCATE=false). Expected result: NO improvement at
# this scale (1109s vs the 1077-1097s baseline range) -- see REPORT.md
# Section 7 row 2 / Section 7b Table C. This is a documented negative result,
# not a bug if your reproduction also shows no improvement.
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"
source "$HERE/run_env_af3.sh"

RUN_NAME="verify_1e66_noprealloc"
OUTPUT_DIR="${OUTPUT_ROOT}/dataset/${RUN_NAME}"
mkdir -p "${OUTPUT_DIR}" "${OUTPUT_ROOT}/traces/raw/${RUN_NAME}"

export DFTRACER_ENABLE=1
export DFTRACER_LOG_FILE="${OUTPUT_ROOT}/traces/raw/${RUN_NAME}/${RUN_NAME}"
export DFTRACER_DATA_DIR="$VAST_ROOT/smol_workflow/msa_db:${OUTPUT_DIR}"
export XLA_PYTHON_CLIENT_PREALLOCATE=false

JSON_PATH="$VAST_ROOT/smol_workflow/pdbbind_casf2016_sample/01_af/1e66.json"

( while true; do
    echo "$(date +%s) $(grep -E 'MemTotal|MemAvailable' /proc/meminfo | tr '\n' ' ')"
    sleep 2
  done ) > "${OUTPUT_ROOT}/artifacts/verify_1e66_noprealloc_meminfo.log" 2>&1 &
METER_PID=$!
trap "kill ${METER_PID} 2>/dev/null || true" EXIT

echo "Starting AF3 1e66 VERIFICATION run: XLA_PYTHON_CLIENT_PREALLOCATE=false"
T0=$(date +%s)
python "${AFPY}" \
  --json_path="${JSON_PATH}" \
  --output_dir="${OUTPUT_DIR}" \
  --flash_attention_implementation=xla
T1=$(date +%s)
kill "${METER_PID}" 2>/dev/null || true
echo "AF3 1e66 verification run (noprealloc) complete. Wall time: $((T1-T0))s (expected ~1109s, no improvement over REPORT.md Section 2 baseline range 1077-1097s)"
