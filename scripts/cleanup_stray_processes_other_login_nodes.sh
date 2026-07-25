#!/usr/bin/env bash
# Kill ALL stray processes owned by the current user on every login node
# EXCEPT the one this script is run from.
#
# Usage: scripts/cleanup_stray_processes_other_login_nodes.sh [-n]
#   -n   dry run: list matching processes on each other login node, don't kill them
set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "-n" ]]; then
    DRY_RUN=1
fi

if ! command -v pdsh >/dev/null 2>&1; then
    echo "error: pdsh not found; this script requires pdsh with the 'login' node group" >&2
    exit 1
fi

if ! command -v nodeset >/dev/null 2>&1; then
    echo "error: nodeset not found; needed to expand the login node hostlist" >&2
    exit 1
fi

# Avoid non-interactive ssh failures (exit 255) on first-time host-key prompts.
export PDSH_SSH_ARGS_APPEND="${PDSH_SSH_ARGS_APPEND:-} -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10"

CURRENT_HOST=$(hostname -s)

NODE_HOSTLIST=$(pdsh -g login -q 2>&1 | awk '/^-- Target nodes --/{getline; print; exit}')
if [[ -z "$NODE_HOSTLIST" ]]; then
    echo "error: could not resolve login node list from pdsh -g login" >&2
    exit 1
fi

LOGIN_NODES=$(nodeset -e "$NODE_HOSTLIST" | tr ' ' '\n' | sort -u)
OTHER_NODES=$(echo "$LOGIN_NODES" | grep -vE "^e?${CURRENT_HOST}$" || true)

if [[ -z "$OTHER_NODES" ]]; then
    echo "No other login nodes found (current: $CURRENT_HOST)."
    exit 0
fi

echo "Current login node: $CURRENT_HOST"
echo "Other login nodes: $(echo "$OTHER_NODES" | tr '\n' ' ')"

TARGET_LIST=$(echo "$OTHER_NODES" | paste -sd, -)

# List every process owned by the user, excluding the pdsh-spawned remote
# shell/pgrep invocation itself (its own PID and its immediate parent bash -c
# wrapper) so the listing/verification isn't polluted by its own plumbing.
PGREP_CMD='pgrep -u $USER -a | grep -v -E "sshd:|pdsh|pgrep -u|bash -c pgrep|bash -c pkill"'

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Dry run — listing all processes owned by $USER on other login nodes:"
    pdsh -w "$TARGET_LIST" "$PGREP_CMD || true"
    exit 0
fi

echo "Killing all stray processes owned by $USER on other login nodes (SIGTERM)..."
pdsh -w "$TARGET_LIST" 'pkill -u $USER || true'

sleep 2
REMAINING=$(pdsh -w "$TARGET_LIST" "$PGREP_CMD" 2>/dev/null || true)
if [[ -n "$REMAINING" ]]; then
    echo "Escalating to SIGKILL for processes that ignored SIGTERM..."
    pdsh -w "$TARGET_LIST" 'pkill -9 -u $USER || true'
    sleep 1
fi

echo "Verifying cleanup..."
REMAINING=$(pdsh -w "$TARGET_LIST" "$PGREP_CMD" 2>/dev/null || true)
if [[ -z "$REMAINING" ]]; then
    echo "Clean: no stray processes owned by $USER remain on any other login node."
else
    echo "Warning: processes still running:"
    echo "$REMAINING"
    exit 1
fi
