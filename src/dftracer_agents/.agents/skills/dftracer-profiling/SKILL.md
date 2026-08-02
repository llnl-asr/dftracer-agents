---
name: dftracer-profiling
description: >
  MANDATORY pipeline self-profiling via MLflow. Every dftracer agent brackets its
  work with profile_step_begin / profile_step_end so the session records what each
  step cost in wall time, tokens, dollars, and retries. The main thread binds the
  session once with profile_bind and writes the report with profile_report. Load
  this skill before executing any pipeline step.
---

# Pipeline Self-Profiling (MLflow)

The dftracer pipeline profiles *itself*. Every step's wall time, token spend,
dollar cost, and retry count is recorded to MLflow and written to
`<workspace>/performance/performance_report.md`. This is not optional telemetry —
it is how we learn which agents are expensive, which steps thrash, and where the
pipeline needs a better tool.

## Bind at session creation — not later

**`profile_bind` is called immediately after `session_create` succeeds**, by
whoever created the session. That is the first moment a session directory exists
to dump into, so it is the first moment the profile has somewhere to live.
Binding creates `<workspace>/performance/` and the MLflow parent run.

Do not defer binding until the first step. Telemetry captured *before* the bind
is retained and attributed to the session — the planning and routing cost that
led up to it is part of what the run cost — but nothing can be written to disk
until the session exists and the bind happens.

## Who calls what

| Caller | Tools | When |
| --- | --- | --- |
| Whoever calls `session_create` | `profile_bind` | Once, in the same breath as session creation |
| Main thread / orchestrator | `profile_report` | Once, after the last step ends |
| Every step agent | `profile_step_begin` / `profile_step_end` | Around that agent's own work |
| Anyone | `profile_status` | Mid-pipeline cost check (cheap, in-memory) |

If the session already existed (a resume), bind once at the top of the resumed
run before dispatching any step.

## The rule for step agents (MANDATORY)

Bracket your entire execution. Do this *after* loading your plan section, so the
step name matches the plan heading verbatim:

```
profile_step_begin(step="STEP 3: dftracer-annotator",
                   agent="dftracer-annotator",
                   notes="<smoke cmd, file count, whatever is diagnostic>")
... do the work ...
profile_step_end(step="STEP 3: dftracer-annotator", status="ok")
```

On failure, close the attempt with the reason, then reopen with the **same**
`step` string to record a retry:

```
profile_step_end(step="STEP 3: dftracer-annotator", status="lint_error",
                 error="clang_lint_annotations: 4 files missing END")
profile_step_begin(step="STEP 3: dftracer-annotator", agent="dftracer-annotator")
```

## Rules

1. **The `step` string is an identity, not a label.** Reuse it exactly across
   retries — same string means "second attempt", a different string means "new
   step". Use the plan's `## STEP N: <agent-name>` heading verbatim.
2. **Always end what you begin.** A step left open is force-closed as
   `superseded` when the next step opens, which silently attributes your cost to
   nobody. Close it yourself, with a real status.
3. **`status="ok"` only when the step actually succeeded.** `failed`, `timeout`,
   and `lint_error` all surface in the report's Rework section — that section is
   the point of the whole exercise. Do not launder a failure into an `ok`.
4. **Never call `profile_bind` from a step agent.** Binding is the orchestrator's
   job; rebinding mid-pipeline splits the MLflow parent run.
5. **Telemetry before `profile_bind` is kept** and attributed to the session — the
   planning and routing that led up to it is part of what the run cost.

## Reading the result

`profile_status()` is served from memory (no MLflow round-trip) and returns
running totals, per-step timing, and attempt counts. `profile_report()` flushes
and writes the markdown report plus `summary.json` and `steps/<n>-<step>.json`.

Call `profile_report()` a few seconds after the last step ends — events buffered
inside Claude Code (`OTEL_LOGS_EXPORT_INTERVAL`, 5 s default) may otherwise miss
the final step.

See [[dftracer-context-economy]] for the companion rule on using the knowledge
graph instead of reading files, which is the other half of keeping a run cheap.

## Troubleshooting: token/dollar figures show 0 even though steps are recorded

`profile_status`/`profile_report` will still show correct step timings and retry
counts, but `events_seen: 0` and `$0.0000`, if OTEL telemetry env vars were not set
**before** the Claude Code process started. Token and dollar figures only populate
when telemetry is exported from process start, not mid-session.

**Required env vars** (must be in the real process environment before launch):
`CLAUDE_CODE_ENABLE_TELEMETRY=1`, `OTEL_LOGS_EXPORTER=otlp`,
`OTEL_METRICS_EXPORTER=otlp`, `OTEL_EXPORTER_OTLP_PROTOCOL=http/json` (protobuf will
NOT parse — the local receiver is stdlib-only), `OTEL_EXPORTER_OTLP_ENDPOINT=
http://127.0.0.1:4318`. Symptom of a bound-but-blind profile:
`performance/otlp/events-*.jsonl` stays 0 bytes.

**The `env` block of `.claude/settings.json` does NOT work for these** — it reaches
tool subprocesses but not the telemetry SDK, which reads its config at process
start, before `settings.json`'s env block is applied to anything.

**Do NOT try to diagnose this by grepping `env` in a Bash tool call.** Claude Code
never re-exports `OTEL_*` to child processes, so they read as unset in BOTH the
working and the broken state — the absence proves nothing either way. The only
reliable signals are `profile_status` -> `events_seen`, and reading the
vscode-server's own `/proc/<pid>/environ` directly.

**Under the VS Code Remote-SSH extension**, the OTEL vars must be in the real
process env of the **vscode-server** itself, which every extension host and
`claude` process inherits. Remote-SSH sources `$HOME/.vscode-server/server-env-setup`
before starting the server — put the exports there. `$HOME/.profile` does **not**
work: Remote-SSH launches the server through a non-login shell, so it is never
read (a `PATH` that looks profile-derived usually actually came from `.bashrc`).
After editing `server-env-setup`, run **Remote-SSH: Kill VS Code Server on Host**
and reconnect — a window reload is NOT enough, since the extension host inherits
env from the already-running server process, not a freshly-read file.

**To distinguish "collector broken" from "nothing being sent"**: POST a synthetic
record to `/v1/logs` and watch `events-*.jsonl` grow. If it grows, the receiver and
MLflow sink are fine and the problem is purely upstream env propagation.

## Permissions

Read-only with respect to source. This skill uses:

- **MCP:** `mcp__dftracer__profile_bind`, `mcp__dftracer__profile_step_begin`,
  `mcp__dftracer__profile_step_end`, `mcp__dftracer__profile_status`,
  `mcp__dftracer__profile_report`
- **Write:** only `<workspace>/performance/` (created by `profile_bind`)
