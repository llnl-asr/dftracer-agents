#!/usr/bin/env bash
# Start a pecan_milan pipeline session against the livai gateway.
#
#   ./scripts/pecan-livai.sh                 # interactive, pipeline prompt loaded
#   ./scripts/pecan-livai.sh --manual        # interactive, no prompt: you drive
#   ./scripts/pecan-livai.sh --headless      # unattended, logs to outputs/
#   MODEL=svc-dldl-gpt-5.5 ./scripts/pecan-livai.sh
#
# Everything the session needs is set up here: the dftracer MCP daemon, the
# venv (which is what puts BOTH mcp servers on PATH), and the livai routing.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${APP:-/usr/workspace/ice4hpc/dyad_pecan/pecan_milan}"
REF="${REF:-main}"
MODEL="${MODEL:-svc-dldl-gpt-5.4}"
PORT="${MCP_PORT:-10000}"          # .mcp.json points here; the daemon defaults to 5000
MODE="interactive"

for arg in "$@"; do
    case "$arg" in
        --manual)   MODE="manual";;
        --headless) MODE="headless";;
        -h|--help)  sed -n '2,12p' "$0"; exit 0;;
        *) echo "unknown option: $arg" >&2; exit 2;;
    esac
done

cd "$REPO"

# ── venv ──────────────────────────────────────────────────────────────────
# Not cosmetic: `phronix-mcp-server` is spawned by name, so without this the
# phronix MCP server simply never starts.
[ -d .venv ] || { echo "no .venv in $REPO" >&2; exit 1; }
# shellcheck disable=SC1091
source .venv/bin/activate

# ── dftracer MCP daemon ───────────────────────────────────────────────────
# It serves over HTTP and outlives any one session, so reuse it when it is
# already up rather than restarting and dropping a running pipeline.
if curl -s -m 5 -o /dev/null "http://127.0.0.1:$PORT/mcp"; then
    echo "dftracer MCP already up on :$PORT"
else
    echo "starting dftracer MCP on :$PORT..."
    ./src/dftracer_agents/dftracer_mcp_server.sh --transport http --port "$PORT"
fi

# ── livai routing ─────────────────────────────────────────────────────────
# Claude Code speaks the Anthropic wire format and ignores OPENAI_* entirely;
# livai serves /v1/messages for these models. ANTHROPIC_BASE_URL wants the
# server ROOT because the CLI appends /v1/messages itself.
export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-https://livai-api.llnl.gov}"
KEY="$("$REPO/scripts/livai-key.sh")"
export ANTHROPIC_AUTH_TOKEN="$KEY" ANTHROPIC_API_KEY="$KEY"
unset KEY
export ANTHROPIC_MODEL="$MODEL"
# The CLI runs a second, "small fast" model for background work. Left unset it
# asks for a Claude model by name, which this gateway answers with a 404 —
# mid-run, and reported as a model error rather than a configuration one.
export ANTHROPIC_SMALL_FAST_MODEL="$MODEL"

# The CLI cannot look up a context window for a gateway model name -- it logs
# `unrecognized_model` and falls back to a small default, so it compacts every
# few turns and the run never builds up any working state. livai reports
# max_input_tokens=1050000 for these models; tell the CLI so.
AUTOCOMPACT="${AUTOCOMPACT:-1000000}"
# --autocompact alone is not enough: it says WHEN to compact, but the CLI still
# clamps it to the window it believes the model has. For a gateway model it
# cannot look one up (`unrecognized_model`) and assumes ~200k, so a 1M request
# silently became a compact at 170k. This declares the real window.
export CLAUDE_CODE_MAX_CONTEXT_TOKENS="${CLAUDE_CODE_MAX_CONTEXT_TOKENS:-1000000}"
# The Read tool truncates at 25k tokens by default, which is its own source of
# re-reading and churn.
export CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS="${CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS:-200000}"
# Tool results are truncated at 25k tokens by default, which is what turned a
# 109k-character memory_read into a file reference the model then had to go
# and read again.
export MAX_MCP_OUTPUT_TOKENS="${MAX_MCP_OUTPUT_TOKENS:-200000}"

echo "model:   $MODEL   via $ANTHROPIC_BASE_URL"
echo "context: autocompact at $AUTOCOMPACT tokens"
echo "app:     $APP ($REF)"
echo "mode:    $MODE"

if [ "$MODE" = "manual" ]; then
    # No first message: the pipeline is driven by hand. `/dftracer-pipeline`
    # is available as a slash command once inside.
    exec claude --autocompact "$AUTOCOMPACT"
fi

export ORCHESTRATOR_MODEL="$MODEL" ANNOTATION_MODEL="$MODEL"
# FRESH_SESSION: a completed session for this app already exists from another
# machine. Without this the orchestrator finds it, reports "already complete"
# and exits after one turn.
export FRESH_SESSION=1

if [ "$MODE" = "headless" ]; then
    mkdir -p outputs
    LOG="$REPO/outputs/pecan_milan-$(echo "$MODEL" | tr -d 'a-z-')-$(date +%m%d-%H%M%S).jsonl"
    echo "log:     $LOG"
    HEADLESS=1 ./src/dftracer_agents/run-pipeline.sh "$APP" "$REF" >"$LOG" 2>&1 &
    echo "started pid $!"
    echo "watch:   .venv/bin/python scripts/watch-pipeline.py -f"
    exit 0
fi

# No --verbose: the default already gives one headline per tool call, and
# Ctrl+O expands a call when you want its arguments and result. Verbose from
# the start buries the headlines under full tool output.
export CLAUDE_EXTRA_FLAGS="--autocompact $AUTOCOMPACT"
exec ./src/dftracer_agents/run-pipeline.sh "$APP" "$REF"
