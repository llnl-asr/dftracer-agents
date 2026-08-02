---
name: feedback-app-exec-cwd
description: "App execution (build/run/smoke-test) must always happen with cwd inside the session workspace, never the project root"
metadata: 
  node_type: memory
  type: feedback
---

**Canonical home:** codified directly in CLAUDE.md's Bash Hard Constraints ("App
execution runs in the session workspace, not the project root") — no separate skill
duplicates this, it's a harness-level rule.

Any command that builds, runs, or tests the traced application must execute with `cwd` set inside `workspaces/<session>/...` (e.g. `build_ann/`, `build/`, `source/`), never the dftracer-agents project root.

**Why:** The project root holds dftracer-agents' own source code; session/app artifacts (built binaries, run scripts, traces) belong to the workspace. Running app commands from the project root mixes concerns and can pick up the wrong build.

**How to apply:** The MCP tools in `dftracer-agents/mcp-tools/tools/session/*.py` (e.g. `session_run_with_dftracer`, `session_run_smoke_test`) already resolve `cwd` correctly by falling back through `build_ann` → `build` → `source` inside the workspace — verified 2026-07-01. When *I* (Claude) manually run/reproduce app commands via Bash (not through an MCP tool), always `cd` into the workspace session folder first. This is now also codified in CLAUDE.md under Bash constraints. See [[feedback_flux_proxy_wrapper]], [[feedback_optimization_pipeline_traces]].
