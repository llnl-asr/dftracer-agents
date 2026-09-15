#!/usr/bin/env bash
# Run the dftracer annotation pipeline with Claude Code.
#
# Usage:
#   ./run-pipeline.sh <git-url> [ref] [smoke-test-cmd] [extra-cmake-flags]
#
# Examples:
#   ./run-pipeline.sh https://github.com/hpc/ior 4.0.0
#   ./run-pipeline.sh https://github.com/hpc/ior main "mpirun -n 2 ./src/ior -t 1m -b 4m"
#
# Requirements:
#   - claude CLI must be on PATH or discoverable in ~/.vscode-server
#   - dftracer MCP server will be auto-registered if not already present

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# The script lives at src/dftracer_agents/, so the repo is TWO levels up.
# One level lands on src/, where no venv has ever existed -- activation then
# silently did nothing and the run failed later at the first MCP call.
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# `.venv` first: that is what the repo actually ships and what has both
# dftracer-agents and phronix installed. The bare `venv` fallback is kept for
# older checkouts. Activation is what puts dftracer-mcp-server on PATH, and a
# missing directory used to be skipped in silence -- the pipeline then failed
# much later, at the first MCP call.
VENV="$REPO_ROOT/.venv"
[[ -d "$VENV" ]] || VENV="$REPO_ROOT/venv"

# ── Model Cascade Configuration ───────────────────────────────────────────
# Hub-and-spoke model routing:
#   ORCHESTRATOR_MODEL — the coordinator that routes tasks and tracks state.
#     Does no code mutation; a lighter/cheaper model is appropriate.
#     Default: haiku-4-5 (fast, cheap, handles MCP tool calls and routing well)
#
#   ANNOTATION_MODEL — the sub-agents that read and mutate source code.
#     Needs deep code understanding; use the best code model available.
#     Default: claude-sonnet-4-8 (strong code reasoning, the pipeline default)
#
# Override via env:
#   ORCHESTRATOR_MODEL=claude-haiku-4-5-20251001 ANNOTATION_MODEL=claude-opus-4-8 \
#     ./run-pipeline.sh https://github.com/hpc/ior
#
# The orchestrator model is passed to claude CLI; the annotation model is
# injected into the session context so annotation sub-agents pick it up.
ORCHESTRATOR_MODEL="${ORCHESTRATOR_MODEL:-claude-haiku-4-5-20251001}"
ANNOTATION_MODEL="${ANNOTATION_MODEL:-claude-sonnet-4-8}"
export ANTHROPIC_MODEL="$ORCHESTRATOR_MODEL"
export CLAUDE_ANNOTATION_MODEL="$ANNOTATION_MODEL"
# The CLI also runs a "small, fast" model for background work (titles, compact
# summaries). Left unset it asks for a Claude model by name, which a gateway
# serving something else answers with a 404 -- mid-run, and surfaced as a model
# error rather than a configuration one. Pin it alongside the orchestrator.
export ANTHROPIC_SMALL_FAST_MODEL="${ANTHROPIC_SMALL_FAST_MODEL:-$ORCHESTRATOR_MODEL}"

# ── Resolve claude binary ──────────────────────────────────────────────────
CLAUDE_BIN="${CLAUDE_BIN:-}"
if [[ -z "$CLAUDE_BIN" ]]; then
    CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"
fi
if [[ -z "$CLAUDE_BIN" ]]; then
    CLAUDE_BIN="$(find "$HOME/.vscode-server" -name "claude" -type f 2>/dev/null | head -1 || true)"
fi
if [[ -z "$CLAUDE_BIN" || ! -x "$CLAUDE_BIN" ]]; then
    echo "Error: claude CLI not found. Set CLAUDE_BIN or add it to PATH." >&2
    echo "  Hint: find it with: find ~/.vscode-server -name claude -type f" >&2
    exit 1
fi

# ── Activate venv so dftracer-mcp-server is on PATH ───────────────────────
if [[ -f "$VENV/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
fi

# ── Parse arguments ────────────────────────────────────────────────────────
APP_URL="${1:-}"
REF="${2:-main}"
SMOKE_CMD="${3:-}"
EXTRA_FLAGS="${4:-}"

if [[ -z "$APP_URL" ]]; then
    echo "Usage: $0 <git-url> [ref] [smoke-test-cmd] [extra-cmake-flags]"
    echo ""
    echo "Examples:"
    echo "  $0 https://github.com/hpc/ior 4.0.0"
    echo "  $0 https://github.com/hpc/ior main 'mpirun -n 2 ./src/ior -t 1m -b 4m'"
    exit 1
fi

# ── Ensure dftracer MCP server is registered ──────────────────────────────
if ! "$CLAUDE_BIN" mcp list 2>/dev/null | grep -q "dftracer"; then
    echo "Registering dftracer MCP server..."
    "$CLAUDE_BIN" mcp add dftracer "$VENV/bin/dftracer-mcp-server"
fi

# Nine defined steps, and left to itself the orchestrator opens four generic
# tasks with "Run dftracer pipeline steps" covering STEP 2 through STEP 8. A
# watcher then sees one task in progress for an hour, and the run's own record
# cannot say which step failed or how often it was retried.
STEP_TRACKING="

STEP TRACKING — the run has to be legible from outside while it happens:

* Create ONE task per pipeline STEP, up front, named for that step (\"STEP 3:
  build app\"). Never a single task covering several steps.
* Mark each in_progress when it starts and completed when it ends, so exactly
  one step is in progress at a time.
* Call profile_step_begin before a step's work and profile_step_end after it,
  with the same name. These markers are what a step's duration, failures and
  retries are measured from; a step that never calls them is invisible.
* When a step fails and you retry it, say so in its task description. The
  retry count is not recorded anywhere else."

# The pipeline exists to be exercised, so a tool that fails has to be fixed
# rather than stepped around. Left to itself the orchestrator does the
# sensible-looking thing -- three MCP calls failed on pecan_milan and it
# finished the work by hand with pip and git, which produces a result and
# teaches the system nothing.
MCP_RULE="

TOOL POLICY — this overrides any instinct to be helpful by getting the job
done another way:

* If an MCP tool exists for a job, it is the ONLY way to do that job. Never
  reimplement it with Bash, even when the tool has just failed and you can see
  the shell command that would work.
* A failing MCP tool is a REPAIR TASK. Read the error as a defect report, fix
  the tool under src/dftracer_agents/mcp_tools/tools/, then re-call it. The run
  continues THROUGH the tool.
* Record the repair: a lesson on the mcp-first skill, and a phronix memory
  entry when the fact is about the workload rather than the tool.
* If it cannot be repaired inside the step's budget, STOP and report the
  defect. Stopping is acceptable; hand-rolling it and reporting success is not.
* Bash stays free for what no tool covers: inspecting the tree, reading logs,
  verifying a fix.

Load the mcp-first skill (skill_load) before the first build step."

# A finished session for the same app is a reason to start a new one, not a
# reason to stop. Asked to run pecan_milan, the orchestrator found a completed
# session in workspaces/, reported "Pipeline Status: Already Complete" and
# exited after one turn -- correct-looking, and useless: that session was built
# on a different machine with a different toolchain.
if [[ "${FRESH_SESSION:-0}" == "1" ]]; then
    FRESH_INSTRUCTION="

THIS IS A NEW RUN ON A DIFFERENT MACHINE. A completed session for this app may
already exist under workspaces/ — it was built elsewhere and its toolchain does
not apply here. Do NOT reuse it, resume it, or report it as the answer. Call
session_create to open a NEW session and run every step from the beginning
against the CURRENT system as reported by system_detect."
else
    FRESH_INSTRUCTION=""
fi

# Not every use of the pipeline wants all nine steps. Annotate, build and
# smoke-test is a complete job on its own -- it answers "does the
# instrumentation compile and still run" -- while the trace/analyse/optimise
# half costs far more and needs a real cluster run to be worth anything.
if [ -n "${STOP_AFTER_STEP:-}" ]; then
    SCOPE_INSTRUCTION="

SCOPE — this run stops after STEP $STOP_AFTER_STEP.

Create tasks for STEP 1 through STEP $STOP_AFTER_STEP only, not all nine. Do
every one of them properly; do not rush them because the run is short. When
STEP $STOP_AFTER_STEP is complete, write the session report covering what was
done, then STOP. Do not begin any later step, and do not treat stopping as
failure -- finishing the scoped steps IS the successful outcome."
else
    SCOPE_INSTRUCTION=""
fi

# A confirmation gate only means something when someone is there to give it.
if [[ "${HEADLESS:-0}" == "1" ]]; then
    STEP6_INSTRUCTION=" At STEP 6 (annotation report), record the report and
continue to the trace run without pausing — this is an unattended run.

NOBODY IS WATCHING. There is no one to answer a question, restart a server or
approve anything, so never end your turn waiting for a person. The dftracer
server runs over HTTP with --reload: when you edit a tool under
src/dftracer_agents/, the server reloads itself within a second or two and the
fix is LIVE IN THIS SESSION. After a repair, just re-call the tool; if it still
fails, wait a moment and retry once before concluding anything. Never report a
repair as deferred to a later session and never ask for a restart -- the
restart has already happened. Only a brand-new or renamed tool needs a client
reconnect, because the tool list is sent when the session connects. Work the
pipeline to the end of its scope and report what you got."
else
    STEP6_INSTRUCTION=" At STEP 6 (annotation report), show the report and wait
for my confirmation before proceeding to the trace run."
fi

# ── Build the initial message ──────────────────────────────────────────────
INITIAL_MSG="/dftracer-pipeline

APP_URL     = $APP_URL
REF         = $REF"

[[ -n "$SMOKE_CMD"   ]] && INITIAL_MSG+="
SMOKE_CMD   = $SMOKE_CMD"
[[ -n "$EXTRA_FLAGS" ]] && INITIAL_MSG+="
EXTRA_FLAGS = $EXTRA_FLAGS"

INITIAL_MSG+="
ANNOTATION_MODEL = $ANNOTATION_MODEL

All inputs are already provided above — skip STEP 1 (Q1-Q4 questions) and go
directly to STEP 0.5 (fetch docs) then STEP 2 (setup). Run the full pipeline
autonomously.$STEP6_INSTRUCTION$FRESH_INSTRUCTION$MCP_RULE$STEP_TRACKING$SCOPE_INSTRUCTION

When spawning annotation sub-agents (via the Agent tool), use model=\"$ANNOTATION_MODEL\"
so that heavier code-mutation work runs on the best available model while you
(the orchestrator) stay on the lighter routing model."

echo "╔════════════════════════════════════════════════════════╗"
echo "║  dftracer Annotation Pipeline — Claude Code            ║"
echo "╠════════════════════════════════════════════════════════╣"
printf "║  App:  %-49s ║\n" "$APP_URL"
printf "║  Ref:  %-49s ║\n" "$REF"
[[ -n "$SMOKE_CMD"   ]] && printf "║  Smoke: %-48s ║\n" "$SMOKE_CMD"
[[ -n "$EXTRA_FLAGS" ]] && printf "║  Flags: %-48s ║\n" "$EXTRA_FLAGS"
echo "╠════════════════════════════════════════════════════════╣"
printf "║  Orchestrator: %-41s ║\n" "$ORCHESTRATOR_MODEL"
printf "║  Annotation:   %-41s ║\n" "$ANNOTATION_MODEL"
echo "╚════════════════════════════════════════════════════════╝"
echo ""
echo "Starting Claude Code (interactive — you will be asked to confirm at Step 6)..."
echo ""

# ── Launch Claude Code with the pipeline as the first message ─────────────
#
# HEADLESS=1 runs the pipeline unattended: no TTY, no confirmation gate, and
# the transcript streams to stdout so it can be redirected to a log. The
# interactive default stays the default because a human at STEP 6 is the
# cheaper way to catch a bad annotation pass before it burns a trace run.
if [[ "${HEADLESS:-0}" == "1" ]]; then
    exec "$CLAUDE_BIN" --print --verbose \
         --output-format stream-json \
         --dangerously-skip-permissions \
         "$INITIAL_MSG"
fi
# shellcheck disable=SC2086
exec "$CLAUDE_BIN" ${CLAUDE_EXTRA_FLAGS:-} "$INITIAL_MSG"
