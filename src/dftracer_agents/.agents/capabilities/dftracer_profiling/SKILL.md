---
name: dftracer_profiling
description: MANDATORY pipeline self-profiling via MLflow. Every dftracer step agent brackets its work with profile_step_begin / profile_step_end so the session records what each step cost in wall time, tokens, dollars and retries; the orchestrator binds the session once with profile_bind right after session_create and writes the report with profile_report; dftracer_profiling_check verifies it. Load before executing any pipeline step, or when token/dollar figures show 0, "no open step to end", steps show as superseded, or profiling says disabled.
---
Cross-references: [[dftracer_graph]], [[dftracer_report]], [[dftracer_skills]]

# Pipeline self-profiling (MLflow)

The dftracer pipeline profiles *itself*. Every step's wall time, token spend, dollar cost and retry count is recorded
to MLflow and written to `<workspace>/performance/performance_report.md` (plus `summary.json` and
`steps/<n>-<step>.json`). This is not optional telemetry: it is how we learn which agents are expensive, which steps
thrash, and where the pipeline needs a better tool. Agents never talk to MLflow or OTLP; these tools drive the
`dftracer-profile-collector` over its control API. When the collector is not running each tool returns `status: ok`
with `profiling: disabled`: an agent never abandons a step because the observer is down (start the stack with
`scripts/dftracer-stack.sh start`).

## Rules

### Bind at session creation, not later
`profile_bind` is called immediately after `session_create` succeeds, by whoever created the session: the first moment a
session directory exists to dump into. Binding creates `<workspace>/performance/` and the MLflow parent run. Do not
defer binding until the first step. Telemetry captured before the bind is retained and attributed to the session (the
planning and routing cost is part of what the run cost), but nothing is written until the bind. On a resume, bind once
at the top of the resumed run before dispatching any step.

### Who calls what

| Caller | Tools | When |
| --- | --- | --- |
| Whoever calls `session_create` | `profile_bind` | Once, in the same breath as session creation |
| Main thread / orchestrator | `profile_report` | Once, after the last step ends |
| Every step agent | `profile_step_begin` / `profile_step_end` | Around that agent's own work |
| Anyone | `profile_status` | Mid-pipeline cost check (cheap, in-memory) |

### The rule for step agents (MANDATORY)
Bracket your entire execution, *after* loading your plan section, so the step name matches the plan heading verbatim:
```
profile_step_begin(step="STEP 3: dftracer-annotator", agent="dftracer-annotator", notes="<smoke cmd, file count, ...>")
... do the work ...
profile_step_end(step="STEP 3: dftracer-annotator", status="ok")
```
On failure, close the attempt with the reason, then reopen with the **same** `step` string to record a retry:
```
profile_step_end(step="STEP 3: dftracer-annotator", status="lint_error", error="clang_lint_annotations: 4 files missing END")
profile_step_begin(step="STEP 3: dftracer-annotator", agent="dftracer-annotator")
```

1. **The `step` string is an identity, not a label.** Same string = another attempt; a different string = a new step.
   Use the plan's `## STEP N: <agent-name>` heading verbatim.
2. **Always end what you begin.** A step left open is force-closed as `superseded` when the next step opens, which
   silently attributes your cost to nobody.
3. **`status="ok"` only when the step actually succeeded.** `failed`, `timeout`, `lint_error` surface in the report's
   Rework section, which is the point of the exercise. Never launder a failure into an `ok`.
4. **Never call `profile_bind` from a step agent.** Rebinding mid-pipeline splits the MLflow parent run.
5. **Telemetry before `profile_bind` is kept** and attributed to the session.

### Reading the result
`profile_status()` is served from memory (no MLflow round-trip): running totals, per-step timing, attempt counts.
`profile_report()` flushes and writes the markdown report plus `summary.json` and `steps/<n>-<step>.json`. Call it a few
seconds after the last step ends: events buffered inside Claude Code (`OTEL_LOGS_EXPORT_INTERVAL`, 5 s default) may
otherwise miss the final step. Then `dftracer_profiling_check(run_id)`.

## Constraints
- Read-only with respect to source; writes only `<workspace>/performance/` (created by `profile_bind`).
- One bind per pipeline; the orchestrator owns the lifecycle.
- See [[dftracer_graph]] (context economy) for the other half of keeping a run cheap: locate with the graph instead of
  reading files.

## Corner cases
- **KNOWN BUG (confirmed, AF3 optimizer session, 2026-08-02): the single-open-step model breaks for PARALLEL component
  dispatch.** Rule 2's `superseded` behavior is right for a sequential retry, wrong when the orchestrator dispatches
  several subagents concurrently against the same nominal step (e.g. the optimizer fanning out
  io/compute/memory/communication): whichever subagent calls `profile_step_begin` LAST force-closes every sibling's open
  step as `superseded`, and each sibling's `profile_step_end` then returns "no open step to end" (reproduced by three of
  four component subagents and the orchestrator in one session). There is one global open-step slot, not one per agent.
  Until fixed at the tool level (concurrently open steps keyed per agent, or several open steps), do not trust per-agent
  timing for parallel fan-out: the orchestrator brackets the WHOLE fan-out step once (begin/end around dispatching and
  collecting all N subagents).
- **Token/dollar figures show 0 although steps are recorded** (`events_seen: 0`, `$0.0000`): the OTEL env was not set
  BEFORE the Claude Code process started; telemetry is configured at process start. Required in the real process env:
  `CLAUDE_CODE_ENABLE_TELEMETRY=1`, `OTEL_LOGS_EXPORTER=otlp`, `OTEL_METRICS_EXPORTER=otlp`,
  `OTEL_EXPORTER_OTLP_PROTOCOL=http/json` (protobuf will NOT parse: the local receiver is stdlib-only),
  `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318`. Symptom of a bound-but-blind profile:
  `performance/otlp/events-*.jsonl` stays 0 bytes.
- The `env` block of `.claude/settings.json` does NOT work for these: it reaches tool subprocesses, not the telemetry
  SDK, which reads its config at process start.
- Do NOT diagnose by grepping `env` in a Bash call: Claude Code never re-exports `OTEL_*` to children, so they read as
  unset in BOTH the working and the broken state. Reliable signals: `profile_status` -> `events_seen`, and the
  vscode-server's own `/proc/<pid>/environ`.
- Under VS Code Remote-SSH the OTEL vars must be in the real env of the **vscode-server**, which every extension host
  and `claude` process inherits: put the exports in `$HOME/.vscode-server/server-env-setup` (sourced before the server
  starts). `$HOME/.profile` does NOT work (non-login shell; a profile-looking PATH usually came from `.bashrc`). After
  editing, run **Remote-SSH: Kill VS Code Server on Host** and reconnect; a window reload is not enough.
- To tell "collector broken" from "nothing being sent": POST a synthetic record to `/v1/logs` and watch
  `events-*.jsonl` grow. If it grows, receiver and MLflow sink are fine; the problem is upstream env propagation.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
`dftracer_profiling_check` failures:
- `report` / `summary` -> `profile_report(flush=True)` a few seconds after the last step (collector running).
- `steps` -> the steps were never bracketed: every step agent calls begin/end.
- `closed` -> end the open step with `profile_step_end(step=<same string>, status=<real outcome>)`.
- `not_superseded` -> a step was left open, or parallel subagents shared the slot: end every step; for fan-out the
  orchestrator brackets the whole fan-out once.
- `honest_status` -> end failed attempts with failed/timeout/lint_error and the error, then reopen with the same step.
- `performance_dir` (inconclusive) -> `profile_bind(run_id)` was never called.
`profiling: disabled` from any tool is not a failure of the step: start the stack to get numbers next time.

## Self-heal
- Tool or collector wrong: fix `mcp_tools/tools/session/profiling.py` / `dftracer_agents/profiling/` in dftracer-agents
  (or `capability_update` this capability) and restart the MCP server or the stack.

## Self-learn
- What is explored here becomes a capability: a new harness's telemetry setup becomes a lesson here or a `tools_<x>`
  capability; see capability_builder. Expensive or thrashing steps found in the report become lessons on the stage
  capability that owns them.

## What works
- Orchestrator: `session_create` -> `profile_bind(run_id, app, system)` -> dispatch steps (each brackets itself) ->
  wait a few seconds -> `profile_report()` -> `dftracer_profiling_check(run_id)`.
- Parallel fan-out (2026-08-02): the orchestrator brackets the whole fan-out once; component subagents do not bracket.
