#!/usr/bin/env bash
# Create this checkout's venv with phronix and dftracer-agents (both editable).
#
#   scripts/setup_env.sh                       # venv at <repo>/.venv; phronix cloned to vendor/phronix (master)
#   VENV=/path/to/venv scripts/setup_env.sh    # put the venv elsewhere
#   EXTRAS=dev scripts/setup_env.sh            # pip extras (default: dev, which brings pytest)
#   PHRONIX_SRC=/path/to/checkout PHRONIX_REF=<branch> PHRONIX_URL=<fork> scripts/setup_env.sh
#
# dftracer-agents owns the performance-engineering capabilities (src/dftracer_agents/.agents/capabilities, dftracer_*).
# It builds on phronix: the capability engine and the general capabilities (workloads, software, systems, tooling,
# process), from a clone in this project's vendor/. What dftracer-agents learns about a phronix capability is committed
# on a contrib/dftracer-agents branch in a worktree of that clone (vendor/contrib/phronix); the capabilities_update MCP
# tool (or python -m phronix.engine update --bootstrap dftracer_agents.capabilities:engine) rebases it and says how to
# push it and open the pull request.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$REPO/.venv}"
PY="${PYTHON:-python3}"
EXTRAS="${EXTRAS:-dev}"
PHRONIX_SRC="${PHRONIX_SRC:-$REPO/vendor/phronix}"
PHRONIX_URL="${PHRONIX_URL:-https://github.com/llnl-asr/phronix.git}"
PHRONIX_REF="${PHRONIX_REF:-master}"
# The site pip config can add an index that does not resolve here.
export PIP_INDEX_URL=https://pypi.org/simple PIP_EXTRA_INDEX_URL= PIP_CONFIG_FILE=/dev/null
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install -q --upgrade pip setuptools wheel
if [ ! -d "$PHRONIX_SRC/.git" ]; then
  mkdir -p "$(dirname "$PHRONIX_SRC")"
  GIT_TERMINAL_PROMPT=0 git clone --branch "$PHRONIX_REF" "$PHRONIX_URL" "$PHRONIX_SRC"
fi
python -m pip install -q -e "$PHRONIX_SRC"
echo "phronix editable from $PHRONIX_SRC ($(git -C "$PHRONIX_SRC" rev-parse --abbrev-ref HEAD))"
python -m pip install -q -e "$REPO[$EXTRAS]"
python - <<'PY'
from dftracer_agents.capabilities import engine
caps = engine().all_caps()
mine = sum(1 for layer, _, _ in caps.values() if layer == "project")
print(f"capabilities: {mine} dftracer-agents, {len(caps) - mine} from phronix")
PY
python -m pip list 2>/dev/null | grep -i -E '^(dftracer|phronix|fastmcp)'
