#!/bin/bash
# Rebuild AF3's venv (from the README recipe, verbatim) plus dftracer's
# Python bindings, into $WS/baseline/af3env. Edit ../config.ini
# (WORKSPACE_ROOT) before running this.
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

VENV_DIR="${WS}/baseline/af3env"
mkdir -p "${WS}/tmp" "${WS}/artifacts"

echo "=== 1/9: module swap to python/3.11.5 ==="
module load python/3.11.5

echo "=== 2/9: create venv (system-site-packages) ==="
python3.11 -m virtualenv --system-site-packages "${VENV_DIR}"

echo "=== 3/9: module load rocm/6.0.0 ==="
module load rocm/6.0.0

echo "=== 4/9: activate venv, install AF3 requirements (editable, --no-deps) ==="
source "${VENV_DIR}/bin/activate"
pip install -r "${WS}/source/llnl-requirements.txt"
pip install --no-deps -e "${WS}/source"

echo "=== 5/9: install ROCm JAX wheels (jaxlib/jax_rocm60_pjrt/jax_rocm60_plugin 0.4.34) ==="
echo "  (fill in wheel URLs for your ROCm/jax release page, download to \$WS/tmp/, then:"
echo "   pip install \$WS/tmp/jaxlib-0.4.34*.whl \$WS/tmp/jax_rocm60_pjrt-0.4.34*.whl \$WS/tmp/jax_rocm60_plugin-0.4.34*.whl)"

echo "=== 6/9: AF3 build_data step ==="
echo "  (locate AF3's own build_data entry point under \$WS/source and run it here)"

echo "=== 7/9: pin pandas/numpy AFTER jax wheels + build_data ==="
pip install pandas==1.5.3 numpy==1.26

echo "=== 8/9: patch jax_triton for ROCm (REPLACE the try/except block, do not append after it) ==="
JT_INIT="${VENV_DIR}/lib/python3.11/site-packages/jax_triton/__init__.py"
if [ -f "${JT_INIT}" ]; then
  cp "${JT_INIT}" "${WS}/artifacts/jax_triton_init.orig.py"
  python3 - "${JT_INIT}" <<'PYEOF'
import re, sys
path = sys.argv[1]
src = open(path).read()
src = re.sub(
    r"try:\s*\n(?:.*\n)*?except AttributeError:\s*\n\s*raise ImportError\([^\)]*\)\s*\n",
    "get_compute_capability = None\nget_serialized_metadata = None\n",
    src, count=1)
open(path, "w").write(src)
PYEOF
else
  echo "  WARNING: ${JT_INIT} not found -- verify venv layout before continuing"
fi

echo "=== 9/9: install dftracer's Python bindings into the SAME venv ==="
export LD_LIBRARY_PATH="/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/opt/cray/pe/cce/20.0.0/cce-clang/x86_64/lib:/usr/lib64:${LD_LIBRARY_PATH}"
pip install pydftracer

echo "=== Verify ==="
python -c "import alphafold3; import jax; print(jax.devices())"
python -c "import dftracer.dftracer"

echo "install.sh complete. See REPORT.md Section 3 for narrative detail."
