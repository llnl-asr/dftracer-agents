#!/bin/bash
# Rebuild the session's dependencies and application from scratch.
# Edit ../config.ini (WORKSPACE_ROOT) before running this.
#
# PECAN is a pure-Python app (no setup.py/compiled build step). Setup is:
#   1. module loads + LD_LIBRARY_PATH + venv creation (see $WS/scripts_env.sh,
#      generated at session time inside the workspace itself)
#   2. dftracer installed in FUNCTION mode (source-annotated, never PRELOAD)
#      via `pip install git+https://github.com/LLNL/dftracer.git` with build
#      isolation LEFT ON (do NOT pass --no-build-isolation)
#   3. app deps (torch/torchvision, h5py against session HDF5, mpi4py,
#      torch-geometric) installed into the SAME venv as dftracer
# See ../patches/annotated.patch for the source-level annotation + optimization
# changes and ../plan/pipeline_plan.md for the full build-flag rationale.
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

if [ ! -f "$WS/scripts_env.sh" ]; then
  echo "ERROR: $WS/scripts_env.sh not found -- this script must run against the" >&2
  echo "session workspace this final_report/ was generated from (rehydrate it first)." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$WS/scripts_env.sh"

echo "== installing dftracer (FUNCTION mode, build isolation ON) =="
pip install git+https://github.com/LLNL/dftracer.git

echo "== installing PECAN application deps into the same venv =="
cd "$WS/annotated/source"
if [ -f requirements.txt ]; then
  pip install -r requirements.txt
fi
python -c "import torch, h5py, mpi4py, torch_geometric" \
  && echo "smoke-import OK: torch, h5py, mpi4py, torch_geometric"

echo "install.sh done."
