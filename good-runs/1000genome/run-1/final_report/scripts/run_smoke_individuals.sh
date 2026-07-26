#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"
set -e
# Standalone individuals.py smoke test, no allocation needed. Expect ~15.61s
# wall time, 2504 per-individual files tarred into one output (REPORT.md S3).
# NOTE: must activate the session's venv -- dftracer's python bindings are
# installed there, not in system python3.
# shellcheck disable=SC1091
source "${WS}/tools/venv/bin/activate"
export DFTRACER_ENABLE=1 DFTRACER_INIT=FUNCTION DFTRACER_INC_METADATA=1 DFTRACER_DATA_DIR=all
export DFTRACER_LOG_FILE="${OUTPUT_ROOT}/traces/smoke_individuals"
mkdir -p "${OUTPUT_ROOT}/traces" "${OUTPUT_ROOT}/dataset/smoke"
# Smoke input files are staged once under WS/dataset/smoke by the session;
# reuse them read-only, but write all outputs into OUTPUT_ROOT so a
# validation run never mutates the original session's dataset dir.
cp -n "${WS}/dataset/smoke/ALL.chr1.tiny.vcf" "${WS}/dataset/smoke/columns.txt" "${OUTPUT_ROOT}/dataset/smoke/" 2>/dev/null || true
cd "${OUTPUT_ROOT}/dataset/smoke"
python3 "${WS}/annotated/bin/individuals.py" ALL.chr1.tiny.vcf 1 0 293 293
