---
name: feedback-profiling-at-session-create
description: profile_bind must fire right after session_create; token/cost needs OTEL env vars exported before Claude Code launches
metadata: 
  node_type: memory
  type: feedback
  
---

**Canonical home:** see the `dftracer-profiling` skill (Troubleshooting section now
carries the OTEL/vscode-server detail from this file in full) and CLAUDE.md Pipeline
Policy rule 7.

Pipeline self-profiling must start at **session creation**, not at the first
pipeline step: `profile_bind` is called immediately after `session_create`
succeeds. Every step agent then brackets its work with `profile_step_begin` /
`profile_step_end`, reusing the plan's `## STEP N: <agent-name>` heading verbatim
as the `step` id (same string = retry, new string = new step). Orchestrator calls
`profile_report` last. Failed attempts get their real status, never `ok`.

**Why:** session creation is the first moment a session directory exists to dump
the profile into. Binding later loses the planning/routing cost and has nowhere
to write.

**How to apply:** `dftracer-session-setup` owns the bind (it owns
`session_create`) and is the one step agent granted `profile_bind`. Codified as
rule 7 in CLAUDE.md and in the `dftracer-profiling` skill.

Critical gotcha: token and dollar figures only populate if telemetry is set
**before** the Claude Code process starts — `CLAUDE_CODE_ENABLE_TELEMETRY=1`,
`OTEL_LOGS_EXPORTER=otlp`, `OTEL_METRICS_EXPORTER=otlp`,
`OTEL_EXPORTER_OTLP_PROTOCOL=http/json` (protobuf will NOT parse; the receiver is
stdlib-only), `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318`. Without them
`profile_status` still reports step timings and retries but shows
`events_seen: 0` and `$0.0000`. Symptom of a bound-but-blind profile:
`performance/otlp/events-*.jsonl` stays 0 bytes.

**The `env` block of `.claude/settings.json` does NOT work for these.** It reaches
tool subprocesses but not the telemetry SDK, which is configured at process start.

Do NOT try to diagnose this by grepping `env` in a Bash tool call. Claude Code
never re-exports `OTEL_*` to child processes, so they read as unset in *both* the
working and the broken state — the absence proves nothing. The only reliable
signals are `profile_status` → `events_seen`, and the vscode-server's own
`/proc/<pid>/environ`.

Under the VS Code extension the OTEL vars must be in the real process env of the
**vscode-server**, which every extension host and `claude` process inherits.
Remote-SSH sources `$HOME/.vscode-server/server-env-setup` before starting the
server; put the exports there. `$HOME/.profile` does **not** work: Remote-SSH
launches the server through a non-login shell, so it is never read (a `PATH` that
looks profile-derived usually came from `.bashrc`). After editing, run
*Remote-SSH: Kill VS Code Server on Host* and reconnect — a window reload is not
enough, since the extension host inherits from the already-running server.

To distinguish "collector broken" from "nothing being sent": POST a synthetic
record to `/v1/logs` and watch `events-*.jsonl` grow. If it grows, the receiver
and MLflow sink are fine and the problem is upstream env.

Both `CLAUDE.md` and `.claude/settings.json` at the project root are symlinks
into `src/dftracer_agents/.agents/workspace/`, recreated on every MCP server
start by `ensure_workspace_setup` (`bootstrap.py`, called from
`mcp_server.py`). **Always edit the `src/` target, never the root symlink** —
the Edit tool refuses to write through symlinks anyway. That is what makes a
change persist and install into new projects.

Related: [[feedback-pipeline-selflearning]], [[feedback-confirm-before-skill-updates]]
