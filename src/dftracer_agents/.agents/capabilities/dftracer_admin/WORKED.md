# What worked: dftracer_admin

## tool:server_reload
Under `dftracer-mcp-server start --ha`: `server_reload()` returns reloaded true; `server_status()` then shows the new generation once it answers.

## tool:server_status
Pass its output to `dftracer_admin_check` to get the action (reload, reconnect, refresh content).
