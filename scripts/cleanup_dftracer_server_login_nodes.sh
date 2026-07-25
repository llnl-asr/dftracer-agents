#!/usr/bin/env bash
# Kill stray dftracer_server processes on every login node of this system.
#
# Usage: scripts/cleanup_dftracer_server_login_nodes.sh [-n]
#   -n   dry run: list matching processes on each login node, don't kill them
set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "-n" ]]; then
    DRY_RUN=1
fi

if ! command -v pdsh >/dev/null 2>&1; then
    echo "error: pdsh not found; this script requires pdsh with the 'login' node group" >&2
    exit 1
fi

# Avoid non-interactive ssh failures (exit 255) on first-time host-key prompts.
export PDSH_SSH_ARGS_APPEND="${PDSH_SSH_ARGS_APPEND:-} -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10"

NODE_HOSTLIST=$(pdsh -g login -q 2>&1 | awk '/^-- Target nodes --/{getline; print; exit}')
if [[ -z "$NODE_HOSTLIST" ]]; then
    echo "error: could not resolve login node list from pdsh -g login" >&2
    exit 1
fi

if command -v nodeset >/dev/null 2>&1; then
    LOGIN_NODES=$(nodeset -e "$NODE_HOSTLIST" | tr ' ' '\n' | sort -u)
else
    LOGIN_NODES="$NODE_HOSTLIST"
fi

echo "Login nodes: $(echo "$LOGIN_NODES" | tr '\n' ' ')"

# -x matches the exact process name (comm), not the full cmdline, so it can
# never false-positive-match this script's own filename or invocation string.
PGREP_CMD='pgrep -u $USER -x -a dftracer_server'

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Dry run — listing dftracer_server processes owned by $USER:"
    pdsh -g login "$PGREP_CMD || true"
    exit 0
fi

echo "Killing dftracer_server processes owned by $USER on all login nodes..."
pdsh -g login 'pkill -u $USER -x dftracer_server || true'

echo "Verifying cleanup..."
sleep 1
REMAINING=$(pdsh -g login "$PGREP_CMD" 2>/dev/null || true)
if [[ -z "$REMAINING" ]]; then
    echo "Clean: no dftracer_server processes remain on any login node."
else
    echo "Warning: dftracer_server processes still running:"
    echo "$REMAINING"
    exit 1
fi
