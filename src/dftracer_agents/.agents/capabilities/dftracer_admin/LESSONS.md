# Lessons: dftracer_admin

- [L-5e2b8c41] Edited tool code had no effect -> a single-process MCP server keeps the code it started with and cannot swap itself without dropping the connection -> reconnect the server (/mcp reconnect) or run dftracer-mcp-server start --ha so server_reload and file changes trigger rolling restarts (2026-11-13)
