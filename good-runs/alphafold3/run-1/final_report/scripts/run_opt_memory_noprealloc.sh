#!/bin/bash
# STEP 8d memory-optimizer 1eby A/B: XLA_PYTHON_CLIENT_PREALLOCATE=false.
# See REPORT.md Section 7 row 2 / Section 7b Table B (-4.9% wall, -77% peak mem).
# Usage: run_opt_memory_noprealloc.sh <tag> <variant: ctrl|noprealloc>
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"
source "$HERE/run_env_af3.sh"

TAG="${1:-a}"; VARIANT="${2:-ctrl}"
RUN_NAME="opt_memory_${VARIANT}_${TAG}"

export DFTRACER_ENABLE=1
export DFTRACER_LOG_FILE="${OUTPUT_ROOT}/traces/raw/${RUN_NAME}"
export DFTRACER_DATA_DIR="$VAST_ROOT/smol_workflow/msa_db:${OUTPUT_ROOT}/dataset/${RUN_NAME}"
mkdir -p "${OUTPUT_ROOT}/traces/raw" "${OUTPUT_ROOT}/dataset/${RUN_NAME}"

if [ "$VARIANT" = "noprealloc" ]; then
  export XLA_PYTHON_CLIENT_PREALLOCATE=false
  echo "VARIANT=noprealloc XLA_PYTHON_CLIENT_PREALLOCATE=false"
else
  echo "VARIANT=ctrl (XLA defaults, preallocate=true)"
fi

OUTPUT_DIR="${OUTPUT_ROOT}/dataset/${RUN_NAME}"
MEMLOG="${OUTPUT_ROOT}/artifacts/opt_memory_${VARIANT}_${TAG}_meminfo.log"
mkdir -p "${OUTPUT_ROOT}/artifacts"
( while true; do
    echo "T=$(date +%s) $(grep -E '^(MemTotal|MemAvailable|MemFree):' /proc/meminfo | tr '\n' ' ')"
    sleep 2
  done ) > "${MEMLOG}" 2>&1 &
PROBE=$!
trap "kill ${PROBE} 2>/dev/null || true" EXIT

T0=$(date +%s)
python "${AFPY}" \
  --json_path="$VAST_ROOT/smol_workflow/pdbbind_casf2016_sample/01_af/1eby.json" \
  --output_dir="${OUTPUT_DIR}" \
  --flash_attention_implementation=xla
T1=$(date +%s)
kill ${PROBE} 2>/dev/null || true
echo "MEMOPT_RESULT tag=${TAG} variant=${VARIANT} wall=$((T1-T0))s meminfo_log=${MEMLOG} (compare to REPORT.md Section 7b Table B: ctrl min 114.4s/122.8GiB peak vs noprealloc max 110.6s/28.4GiB peak)"
