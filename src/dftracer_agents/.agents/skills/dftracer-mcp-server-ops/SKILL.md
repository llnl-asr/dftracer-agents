---
name: dftracer-mcp-server-ops
description: Run, inspect, refresh and reload the dftracer MCP server from a Claude session without restarting or reconnecting - zero-downtime (--ha) mode, server_status / server_reload / server_refresh_content, what refreshes by itself and what needs a rolling restart. Load when a tool is missing or stale after a code change, when serving several users, or before restarting the server.
---

## Normal operation: nothing to do

| change | what happens | you do |
|---|---|---|
| skill / agent / resource text | read at call time; the supervisor re-links new ones | nothing |
| package **code** (tool bodies, new tools) | `--ha`: new generation starts, traffic switches when it answers, old one drains | nothing (or `server_reload` to do it now) |
| a worker dies | replaced by the supervisor; the other workers keep answering | nothing |
| the new code is broken | the old generation keeps serving, the error is in `server_status` | fix the code; the next edit is picked up |

Start the zero-downtime daemon: `dftracer-mcp-server start --ha [--workers 2]` (HTTP; point clients at
`http://host:5000/mcp`). It runs stateless workers behind a gateway on that port, so a rolling restart loses no
session. A plain `start` / stdio server is a single process and cannot swap itself without dropping the connection.

## From a Claude session (MCP tools, service `admin`, included in `both`)

- `server_status()` - mode, uptime, `code_changed_since_start`, `content_changed_since_start`; under `--ha` also the
  supervisor's generation, workers, backup workers, swaps, `last_error`, requests served, and an `advice` line.
- `server_reload()` - ask the supervisor for a rolling restart now (only in `--ha` mode; otherwise it says how to enable it).
- `server_refresh_content()` - re-link skills/agents now (idempotent).

CLI equivalents: `dftracer-mcp-server status | reload | stop`.

## Edge cases

- A tool you just added is invisible to a client that is already connected: FastMCP does not emit
  `notifications/tools/list_changed`; the next request through the gateway lists it (stateless), a single-process
  server needs `/mcp` reconnect.
- `last_error` set + `source_current: false` means the newest edit did not start (see `worker-<generation>.err` in
  `/tmp/$USER/dftracer-agents/`); the old generation is still serving.
- Do not hand-restart a `--ha` daemon with `kill`: use `reload` (rolling) or `stop` (graceful).

Sources: behaviour is implemented in `src/dftracer_agents/ha.py` and `mcp_tools/tools/dftracer/admin_service.py`
and exercised by `test/test_ha_gateway.py` (experiment: calls issued through the gateway during a rolling restart, a
failed new generation and a killed worker - no request failed).
