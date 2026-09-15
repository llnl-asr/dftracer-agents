#!/usr/bin/env bash
# The control arm for eval 5: the same task, same model, no phronix and no
# dftracer MCP — just Claude Code and a shell.
#
#   ./scripts/pecan-bare.sh              # interactive
#   ./scripts/pecan-bare.sh --headless   # unattended, logs to outputs/
#
# Run from a scratch directory so the project's .claude/ (agents, skills,
# hooks, both MCP servers) is not in scope. Sharing the project directory
# would quietly give the "bare" arm the pipeline's skills and make the
# comparison meaningless.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${APP:-/usr/workspace/ice4hpc/dyad_pecan/pecan_milan}"
MODEL="${MODEL:-svc-dldl-gpt-5.4}"
WORK="${WORK:-$REPO/workspaces/_bare/$(date +%Y%m%d_%H%M%S)}"
MODE="interactive"
[ "${1:-}" = "--headless" ] && MODE="headless"

mkdir -p "$WORK"
cd "$WORK"

# Same gateway and model as the phronix arm: the comparison is about the
# framework, so everything else has to match.
export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-https://livai-api.llnl.gov}"
KEY="$("$REPO/scripts/livai-key.sh")"
export ANTHROPIC_AUTH_TOKEN="$KEY" ANTHROPIC_API_KEY="$KEY"; unset KEY
export ANTHROPIC_MODEL="$MODEL" ANTHROPIC_SMALL_FAST_MODEL="$MODEL"

# The task, stated the way someone without the pipeline would have to state it.
TASK="Profile and optimise the I/O of the application at $APP using DFTracer.

Work in $WORK. The app is PECAN/MILAN, a PyTorch + PyTorch-Geometric ML
application (Python, MPI, HDF5). You are on the corona cluster (AMD MI50,
flux launcher, no sudo).

Do the whole job: get the application running, install and enable DFTracer,
annotate the source so the trace is meaningful, collect a baseline trace,
analyse it, propose and apply an I/O optimisation, and measure whether it
helped. Report what you changed and the measured difference."

echo "bare arm — model $MODEL, workdir $WORK"
if [ "$MODE" = "headless" ]; then
    mkdir -p "$REPO/outputs"
    LOG="$REPO/outputs/pecan_bare-$(date +%m%d-%H%M%S).jsonl"
    echo "log: $LOG"
    claude --print --verbose --output-format stream-json \
           --dangerously-skip-permissions "$TASK" >"$LOG" 2>&1 &
    echo "started pid $!"
    exit 0
fi
exec claude "$TASK"
