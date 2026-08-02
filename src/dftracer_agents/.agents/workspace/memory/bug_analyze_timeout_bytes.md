---
name: bug-analyze-timeout-bytes
description: "MCP analyze tool crashed with \"can't concat str to bytes\" on traces where dfanalyzer hangs at dask teardown"
metadata: 
  node_type: memory
  type: project
---

**Canonical home:** see the `dftracer-annotation-lessons` skill (`LESSONS_LOG.md`, the
2026-07-08 bytes-decode follow-up entry right after the original timeout-hang entry).

In `src/dftracer_agents/mcp_tools/tools/dftracer/dfanalyzer_service.py`, the `analyze` tool's `subprocess.run(..., text=True, timeout=300)` TimeoutExpired handler assumed `exc.stdout/stderr` were str, but CPython returns them as **bytes** even under `text=True`. So `stderr += "\n[...]"` raised `TypeError: can't concat str to bytes`, turning the intended "timeout-as-success after output" path into a hard tool failure.

Triggered by traces where dfanalyzer's dask LocalCluster hangs on teardown after printing all real output (e.g. h5bench-run1). Fixed 2026-07-08 by decoding bytes→str in the except handler. Requires MCP server restart to load. See [[project_claude_agent_models]] (tool-code changes need reload).
