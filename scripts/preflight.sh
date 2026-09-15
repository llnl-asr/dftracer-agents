#!/usr/bin/env bash
# Check everything a good pipeline run depends on, before spending an hour
# discovering one of them was wrong. Each line here is something that has
# actually broken a run.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
fail=0
ok()   { printf '  ok    %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fail=1; }

echo "preflight: $REPO"

[ -d .venv ] && ok "venv present" || bad "no .venv"
.venv/bin/python -c "import phronix, dftracer_agents" 2>/dev/null \
  && ok "both packages import" || bad "phronix/dftracer_agents do not import"

# Each server must actually complete an MCP handshake. Over stdio there is no
# port to poll, and the ways these fail are silent: the dftracer console script
# daemonises (printing to stdout, which IS the protocol channel) unless given
# `run --transport stdio`, and phronix is spawned by bare name so it needs the
# venv on PATH.
probe='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"preflight","version":"1"}}}'
for server in dftracer phronix; do
    cmd=$(.venv/bin/python -c "
import json,shlex
m=json.load(open('.mcp.json'))['mcpServers']['$server']
print(shlex.join([m['command'], *m.get('args', [])]))" 2>/dev/null)
    if [ -z "$cmd" ]; then bad "$server not configured in .mcp.json"; continue; fi
    reply=$(printf '%s\n' "$probe" | timeout 60 env PATH="$PWD/.venv/bin:$PATH" \
            sh -c "$cmd" 2>/dev/null | head -c 60)
    case "$reply" in
        *'"result"'*) ok "$server MCP handshake (stdio)";;
        *)            bad "$server MCP did not hand shake:  $cmd";;
    esac
done

# Benchmark skills leaking in from phronix's store swamps the real ones.
bench=$(ls .claude/skills 2>/dev/null | grep -cE 'astropy|django|sympy|scikit|matplotlib|seaborn|pytest-dev|pylint|sphinx|xarray|flask|requests')
[ "$bench" = "0" ] && ok "no benchmark skills in .claude/skills ($(ls .claude/skills | wc -l) total)" \
                   || bad "$bench benchmark skills leaked into .claude/skills"
dangling=$(find .claude/skills -maxdepth 1 -xtype l 2>/dev/null | wc -l)
[ "$dangling" = "0" ] && ok "no dangling skill links" || bad "$dangling dangling skill links"

# The rule has to be reachable BOTH ways: the native Skill tool reads
# .claude/skills, phronix's skill_load reads the project layer.
[ -f .claude/skills/mcp-first/SKILL.md ] && ok "mcp-first reachable by the Skill tool" \
                                         || bad "mcp-first not linked into .claude/skills"

# auto mode needs a safety classifier that only Anthropic hosts; on a gateway
# every Bash call fails with "temporarily unavailable".
mode=$(.venv/bin/python -c "import json;print(json.load(open('.claude/settings.local.json')).get('permissions',{}).get('defaultMode'))" 2>/dev/null)
[ "$mode" = "acceptEdits" ] && ok "defaultMode=acceptEdits (auto mode cannot classify on a gateway)" \
                            || bad "defaultMode=$mode — set acceptEdits, and do not shift+tab into auto"

[ -x scripts/livai-key.sh ] && [ -n "$(scripts/livai-key.sh 2>/dev/null)" ] \
  && ok "livai key readable" || bad "livai key helper failed"

# The instructions that make a run measurable.
for rule in FRESH_SESSION MCP_RULE STEP_TRACKING; do
    grep -q "$rule" src/dftracer_agents/run-pipeline.sh && ok "launcher carries $rule" \
                                                        || bad "launcher missing $rule"
done

running=$(pgrep -f "local/bin/claude --print" | wc -l)
[ "$running" -eq 0 ] && ok "no headless run competing" || echo "  warn  $running headless run(s) still going"

echo
[ "$fail" -eq 0 ] && echo "READY" || echo "NOT READY — fix the FAIL lines above"
exit "$fail"
