---
name: feedback-mcp-reload-and-tool-first-annotation
description: Fix the annotation tool instead of hand-renaming/rewriting files; dftracer MCP server --reload restarts on every tool edit and drops in-flight agent calls
metadata:
  type: feedback
---

When annotation tools mis-handle a project (e.g. `.C` files treated as C), fix the MCP tool (add a parameter like `cpp_extensions`) and re-run `clang_annotate_project`; never rename source files or hand-annotate. The user stopped a subagent that renamed 948 `.C` files to `.cpp` and asked for the tool to be used instead, and asked for project annotation to be parallelized inside the tool (process pool) rather than via many agents.

The dftracer MCP server runs with `--reload`: every edit to a `.py` under the package restarts it, which drops in-flight calls ("session expired" / "not connected") for the main thread and any background agent. A client may also show a cached tool schema after reload.

**Why:** hand workarounds bypass the self-improving tools; reloads silently push agents into manual fallbacks.
**How to apply:** batch tool edits, then wait ~30 s and retry a failed MCP call before any fallback; tell background agents to retry rather than work around. Verify a reload landed by comparing the server child start time with the edited file mtime. Related: [[feedback_never_manual_when_restart_needed]].
