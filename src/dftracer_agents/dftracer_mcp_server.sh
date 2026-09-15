#!/usr/bin/env bash
# DFTracer MCP server launcher.
#
# stdio (default): runs in the foreground — suitable for Claude Code / MCP
#   clients that spawn the server as a subprocess over stdin/stdout.
#
# http: backgrounds the server, writes a PID file, and manages port cleanup.
#
# Usage:
#   ./dftracer_mcp_server.sh                              # stdio (default)
#   ./dftracer_mcp_server.sh --service both
#   ./dftracer_mcp_server.sh --transport http             # HTTP on port 5000
#   ./dftracer_mcp_server.sh --transport http --port 8080
#   ./dftracer_mcp_server.sh --transport streamable-http
#   ./dftracer_mcp_server.sh stop                         # stop HTTP server

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Two levels up: this script lives in src/dftracer_agents/, so one level
# lands on src/, where no venv exists.
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV="$REPO_ROOT/.venv"
[[ -d "$VENV" ]] || VENV="$REPO_ROOT/venv"
PID_FILE="/tmp/dftracer-agents.pid"
LOG_FILE="/tmp/mcp_server.log"
PORT="${MCP_PORT:-5000}"
SERVICE="${MCP_SERVICE:-both}"
TRANSPORT="stdio"

# ── parse args ─────────────────────────────────────────────────────────────
STOP_ONLY=false
EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    stop) STOP_ONLY=true; shift ;;
    --port) PORT="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --transport) TRANSPORT="$2"; shift 2 ;;
    *) EXTRA_ARGS+=("$1"); shift ;;
  esac
done

# ── resolve the server command ──────────────────────────────────────────────
# Prefer the venv-installed entry point; fall back to python -m.
if [[ -x "$VENV/bin/dftracer-mcp-server" ]]; then
  SERVER_CMD=("$VENV/bin/dftracer-mcp-server")
else
  PYTHON="$VENV/bin/python"
  if [[ ! -x "$PYTHON" ]]; then
    PYTHON=$(command -v python3 || command -v python)
  fi
  SERVER_CMD=("$PYTHON" -m dftracer_agents.mcp_server)
fi

# ── stdio mode: run in foreground ──────────────────────────────────────────
if [[ "$TRANSPORT" == "stdio" ]]; then
  $STOP_ONLY && { echo "[mcp] Nothing to stop (stdio has no background process)."; exit 0; }
  exec "${SERVER_CMD[@]}" \
    --service "$SERVICE" \
    --transport stdio \
    "${EXTRA_ARGS[@]}"
fi

# ── HTTP mode: manage background server ────────────────────────────────────

# Stop existing server
if [[ -f "$PID_FILE" ]]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "[mcp] Stopping existing server (PID $OLD_PID)…"
    kill "$OLD_PID" 2>/dev/null || true
    sleep 1
    kill -9 "$OLD_PID" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi

# Kill any stale process that still owns the port
STALE=$(lsof -ti "tcp:$PORT" 2>/dev/null | head -1 || true)
if [[ -n "$STALE" ]]; then
  echo "[mcp] Killing stale process $STALE on port $PORT…"
  kill "$STALE" 2>/dev/null || true
  sleep 1
fi

$STOP_ONLY && { echo "[mcp] Server stopped."; exit 0; }

echo "[mcp] Starting DFTracer MCP server (service=$SERVICE transport=$TRANSPORT port=$PORT)…"
"${SERVER_CMD[@]}" \
  --service "$SERVICE" \
  --transport "$TRANSPORT" \
  --port "$PORT" \
  "${EXTRA_ARGS[@]}" \
  >> "$LOG_FILE" 2>&1 &

SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

# Wait briefly and confirm the server started
sleep 3
if ! kill -0 "$SERVER_PID" 2>/dev/null; then
  echo "[mcp] Server exited unexpectedly. Last log lines:"
  tail -20 "$LOG_FILE"
  exit 1
fi

echo "[mcp] Server running (PID $SERVER_PID) → http://localhost:$PORT/mcp"
echo "[mcp] Logs: $LOG_FILE"
echo "[mcp] PID:  $PID_FILE"
