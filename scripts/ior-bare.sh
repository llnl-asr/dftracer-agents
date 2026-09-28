#!/usr/bin/env bash
# eval5 control arm for IOR: the same annotate -> build -> smoke-test job,
# same model, but with nothing of ours in scope -- no dftracer MCP, no phronix,
# no skills, no hooks, no custom agents. Just the harness, the model and a
# shell, given the task in plain English.
#
#   ./scripts/ior-bare.sh              # interactive
#   ./scripts/ior-bare.sh --headless   # unattended, logs to outputs/
#
# Isolation (each flag is load-bearing; dropping one leaks the treatment arm):
#   cwd outside the repo   project .claude/ (agents, skills, hooks) out of scope
#   --restricted           ignores user, project and local settings files, and
#                          confines the file tools to --add-dir
#   --tools ...            puts back the built-in tools --restricted removes
#   --strict-mcp-config    ignores every MCP config on the machine
#   --mcp-config {}        ...and supplies an empty one, so zero MCP tools
#   --disable-slash-commands   no skills, including /dftracer-pipeline
#
# Note: --restricted refuses bypassPermissions, so the control runs under
# acceptEdits. Its writes are confined to $WORK, which is what we want anyway.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_URL="${APP_URL:-https://github.com/hpc/ior}"
REF="${REF:-main}"
RUN_CMD="${RUN_CMD:-flux run -n 2 ./src/ior -t 1m -b 4m}"
MODEL="${MODEL:-sonnet}"
# OUTSIDE the repo, deliberately. Even with MCP and skills off, running inside
# the tree lets the project's CLAUDE.md be discovered as context, which would
# hand the control arm the pipeline's hard-won knowledge (POSIX-only build,
# mvapich2 quirks) and destroy the comparison.
WORK="${WORK:-${BARE_ROOT:-/usr/WS2/haridev/eval5-bare}/ior-$(date +%Y%m%d_%H%M%S)}"

# Budget, from the phronix arm's own good run (run 3: ~33 min wall, 472
# assistant turns, 399 tool calls to reach the end of the annotate/build/smoke
# scope). The control gets 2x the wall clock so a loss cannot be blamed on a
# tighter leash than the treatment had. There is no --max-turns in this CLI
# build, so wall clock is the only enforceable budget.
WALL="${WALL:-4000}"

MODE="interactive"
[ "${1:-}" = "--headless" ] && MODE="headless"

mkdir -p "$WORK"
cd "$WORK"

# Same gateway and model as the treatment arm: the comparison is about the
# framework, so everything else has to match.
if [ -n "${USE_LIVAI:-}" ]; then
    export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-https://livai-api.llnl.gov}"
    KEY="$("$REPO/scripts/livai-key.sh")"
    export ANTHROPIC_AUTH_TOKEN="$KEY" ANTHROPIC_API_KEY="$KEY"; unset KEY
    export ANTHROPIC_MODEL="$MODEL" ANTHROPIC_SMALL_FAST_MODEL="$MODEL"
fi

# The task, stated the way someone without the pipeline would have to state it.
# It names the same five steps the pipeline performs, so both arms are asked
# for the same artifacts -- but it gives no procedure, no tools and no skills.
TASK="Add DFTracer instrumentation to the IOR benchmark and prove it works.

Work entirely inside $WORK. You are on the corona cluster (AMD MI50, flux
launcher, no sudo, modules available via 'module load').

Do all of this:
1. Clone $APP_URL at ref $REF into a 'source' directory here.
2. Install DFTracer from the LLNL GitLab, develop branch, into a virtualenv
   here, so IOR can be linked against it:
     pip install \"git+https://github.com/llnl-asr/dftracer.git@develop\"
   Use that URL and that branch. Do not substitute a GitHub mirror: the
   GitHub HTTPS clone does not work from this account.
3. Annotate IOR's C sources so a trace is meaningful: include the DFTracer
   header and add region annotations to the I/O-relevant functions.
4. Build the annotated IOR, linking DFTracer. It must compile and link.
5. Smoke test it: run '$RUN_CMD' and confirm the annotated binary runs to
   completion with exit status 0. Report the write and read bandwidth it
   prints. (Trace collection is NOT in scope for this run -- stop after the
   smoke test.)

Report what you annotated, whether the build succeeded and linked DFTracer,
and whether the smoke test exited 0. NOBODY IS WATCHING -- there is no one to answer a question or
approve anything, so never end your turn waiting for a person. Work the job to
completion and report what you got."

TOOLS="Bash,Read,Edit,Write,Glob,Grep,TodoWrite,WebFetch"

# acceptEdits auto-approves edits but NOT Bash, and --restricted refuses
# bypassPermissions -- so an unattended control gets "This command requires
# approval" on every shell command and dies having done nothing. That would
# measure our permission config, not the model. --restricted still honours
# --settings, so grant the tools outright there. Writes stay confined to
# $WORK by --restricted's working-directory rule.
SETTINGS_JSON="$(printf '{"permissions":{"allow":["Bash","Read","Edit","Write","Glob","Grep","WebFetch","TodoWrite"],"defaultMode":"acceptEdits"}}')"

COMMON=(--restricted --tools "$TOOLS"
        --strict-mcp-config --mcp-config '{"mcpServers":{}}'
        --disable-slash-commands
        --settings "$SETTINGS_JSON"
        --permission-mode acceptEdits
        --add-dir "$WORK"
        --model "$MODEL")

echo "bare arm — model $MODEL, workdir $WORK, budget ${WALL}s"
if [ "$MODE" = "headless" ]; then
    mkdir -p "$REPO/outputs"
    STAMP="$(date +%m%d-%H%M%S)"
    RAW="$REPO/outputs/ior_bare-$STAMP.jsonl"
    TXT="$REPO/outputs/ior_bare-$STAMP.log"
    printf 'RAW=%s\nTXT=%s\nWORK=%s\n' "$RAW" "$TXT" "$WORK" > /tmp/ior_bare_paths
    echo "log: $TXT"
    timeout "$WALL" claude --print --verbose --output-format stream-json \
           "${COMMON[@]}" "$TASK" >"$RAW" 2>&1 &
    echo "started pid $!"
    exit 0
fi
exec claude "${COMMON[@]}" "$TASK"
