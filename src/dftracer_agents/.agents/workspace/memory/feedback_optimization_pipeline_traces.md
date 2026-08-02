---
name: feedback-optimization-pipeline-traces
description: "session_optimization_iteration requires traces in the session workspace, not Lustre — write DFTRACER_LOG_FILE to <WS>/traces/ not /p/lustre5/"
metadata: 
  node_type: memory
  type: feedback
---

**Canonical home:** codified in CLAUDE.md Pipeline Policy rule 11, and see
`workload-scaffold`/`feedback_lustre_io` for the applied pattern.

When running `session_optimization_iteration`, the tool looks for trace files inside the session workspace at `<WS>/traces/` (under `$PROJECT_ROOT/workspaces/<session>/traces/`).

If `DFTRACER_LOG_FILE` points to Lustre (`/p/lustre5/...`), the tool returns `"trace_files": []` and finds 0 bottlenecks — silently giving a false "optimization complete" result.

**Why:** The MCP session infrastructure only scans the project-local workspace for trace files. Lustre paths are outside its scan scope.

**How to apply:** For standalone runs (not using the optimization pipeline), Lustre is fine. For any run that feeds into `session_optimization_iteration` or `session_analyze_traces`, set `DFTRACER_LOG_FILE` to the session workspace traces dir:

```bash
export DFTRACER_LOG_FILE=$PROJECT_ROOT/workspaces/<session>/traces/<app_name>
```

Codified in SKILL.md NEVER list.
