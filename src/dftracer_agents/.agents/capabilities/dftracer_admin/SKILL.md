---
name: dftracer_admin
description: Inspect and refresh the dftracer MCP server a session talks to. server_status says how it serves (stdio, plain http, zero-downtime worker), for how long, and whether package code or skills/agents changed since it started; server_reload performs a rolling restart under the --ha supervisor; server_refresh_content re-links skills and agents; dftracer_admin_check rules on a status report. Load after changing dftracer-agents code or skills, or when a tool behaves like old code.
---
Cross-references: [[dftracer_skills]], [[dftracer_bridge]]

# dftracer MCP server administration

## Rules
- Nothing here is needed in normal operation: under the supervisor (`dftracer-mcp-server start --ha`) the server
  refreshes by itself when the source changes.
- After editing tool code: `server_status()` then `dftracer_admin_check(<its output>)`. Under the supervisor,
  `server_reload()` performs the rolling restart now (the new generation starts, traffic switches when it answers, the
  old one drains; the old one keeps serving if the new one fails); poll `server_status` until the generation changes.
- A single-process server (stdio or plain http) cannot swap itself without dropping the connection: restart it (in
  Claude Code: `/mcp reconnect`) or run `dftracer-mcp-server start --ha` for zero-downtime reloads.
- Skills and agents are read at call time; `server_refresh_content()` only re-links the symlinks Claude Code discovers
  (idempotent).
- Never fall back to manual work when a tool fix needs a server restart: ask for the restart.

## Constraints
- `server_reload` signals the supervisor (SIGHUP to `DFTRACER_SUPERVISOR_PID`); it does nothing outside it.
- Do not start a second server with another session root for pipeline work.

## Corner cases
- `code_changed_since_start` compares code fingerprints taken when the admin service started; content changes
  (skills/agents) do not require a restart.
- A failed new generation leaves the old one serving and records `last_error` in the supervisor state: fix the code and reload again.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
`dftracer_admin_check` failures: `code_current` -> `server_reload()` under the supervisor, otherwise reconnect the MCP
server; `supervisor_error` -> read `last_error`, fix the code that failed to import or start, reload; `content_current`
-> `server_refresh_content()`; `shape` / `json` -> pass `server_status()`'s output unchanged. Then status and check again.

## Self-heal
- A tool here is wrong: fix `mcp_tools/tools/dftracer/admin_service.py` or `dftracer_agents/ha.py` (dftracer-agents)
  or `capability_update` this capability.

## Self-learn
- What is explored here becomes a capability: a deployment-specific way of running the server (a system's service
  manager, a port policy) becomes a `system_<machine>` or `tools_<x>` capability; see capability_builder.

## What works
- Edit tool code -> `server_status` -> `server_reload` (supervisor) -> `server_status` shows a new generation and
  `code_changed_since_start: false` -> `dftracer_admin_check` pass.
