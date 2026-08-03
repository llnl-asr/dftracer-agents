#!/bin/bash
# STEP 8a compute-optimizer 1eby variants (control, triton attention,
# autotune=4, tight-bucket 248, JIT compile cache). All are launch-flag-only
# -- no source edits. See REPORT.md Section 7 rows 3-6.
# Usage: run_compute_opt1.sh <variant: control|triton|autotune|tightbucket|jitcache>
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"
source "$HERE/run_env_af3.sh"

VARIANT="${1:-control}"
RUN_NAME="compute_opt1_${VARIANT}"

AF3_FLASH="xla"
AF3_BUCKETS=""
AF3_CACHE_DIR=""
AF3_EXTRA_XLA=""

case "$VARIANT" in
  control)     ;;
  triton)      AF3_FLASH="triton" ;;
  autotune)    AF3_EXTRA_XLA="--xla_gpu_autotune_level=4" ;;
  tightbucket) AF3_BUCKETS="256,512,768,1024,1280,1536,2048,2560,3072,3584,4096,4608,4864" ;;  # 248-token tight variant point; see REPORT.md row 5
  jitcache)    AF3_CACHE_DIR="${OUTPUT_ROOT}/tmp/jax_cache"; mkdir -p "${AF3_CACHE_DIR}" ;;
  *) echo "unknown variant: $VARIANT (expected control|triton|autotune|tightbucket|jitcache)" >&2; exit 1 ;;
esac

if [ -n "${AF3_EXTRA_XLA}" ]; then
  export XLA_FLAGS="${XLA_FLAGS} ${AF3_EXTRA_XLA}"
fi

OUTPUT_DIR="${OUTPUT_ROOT}/dataset/${RUN_NAME}"
mkdir -p "${OUTPUT_DIR}"
export DFTRACER_ENABLE=1
export DFTRACER_LOG_FILE="${OUTPUT_ROOT}/traces/${RUN_NAME}"
export DFTRACER_DATA_DIR="$VAST_ROOT/smol_workflow/msa_db:${OUTPUT_DIR}"
mkdir -p "${OUTPUT_ROOT}/traces"

JSON_PATH="$VAST_ROOT/smol_workflow/pdbbind_casf2016_sample/01_af/1eby.json"

ARGS=( --json_path="${JSON_PATH}" --output_dir="${OUTPUT_DIR}"
       --flash_attention_implementation="${AF3_FLASH}" )
[ -n "${AF3_BUCKETS}" ]   && ARGS+=( --buckets="${AF3_BUCKETS}" )
[ -n "${AF3_CACHE_DIR}" ] && ARGS+=( --jax_compilation_cache_dir="${AF3_CACHE_DIR}" )

echo "=== VARIANT ${RUN_NAME} flash=${AF3_FLASH} buckets=${AF3_BUCKETS:-default} cache=${AF3_CACHE_DIR:-none}"
T0=$(date +%s)
python "${AFPY}" "${ARGS[@]}"
T1=$(date +%s)
echo "=== VARIANT ${RUN_NAME} WALL_SECONDS=$((T1-T0)) (see REPORT.md Section 7 Table A for expected pattern: all no-op/regression at 1eby, jitcache expected to crash with HIP_ERROR_OutOfMemory unless run with --xla_gpu_enable_command_buffer= appended to XLA_FLAGS)"
