#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
source ${WS}/scripts/env.sh 2>/dev/null || true
cd ${WS}
dfanalyzer trace_path=${WS}/baseline/traces/compact \
  analyzer/preset=generic \
  "analyzer.checkpoint=True" \
  "analyzer.checkpoint_dir=${WS}/artifacts/analyzer_checkpoint_generic" \
  "cluster.n_workers=8" \
  "view_types=[time_range]"
