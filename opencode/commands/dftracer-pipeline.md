---
description: Run the full DFTracer annotation, trace, and optimization pipeline.
name: dftracer-pipeline
agent: dftracer-pipeline
---

# /dftracer-pipeline

Before running, read `.agents/skills/dftracer-annotation-lessons/SKILL.md` and apply every lesson that matches the current app or language.

Parse `$ARGUMENTS` for the following named parameters:
  - `url=...`
  - `ref=...`
  - `smoke_cmd=...`
  - `extra_flags=...`
  - `run_id=...`

Pass these values to the `dftracer-pipeline` agent. If `run_id` is supplied, skip the clone/input questions and jump directly to the annotation/build stages using the existing workspace.

If arguments are missing, ask the user the following four questions one at a time and wait for each answer:

  1. "What is the Git URL of the application you want to annotate?"
  2. "Which branch or tag? (default: main)"
  3. "Smoke test command? (leave blank to auto-detect)"
  4. "Extra CMake/configure build flags? (leave blank to skip)"

Then invoke the `dftracer-pipeline` agent with the collected values.

Never manually insert DFTracer macros into source files; annotation must be done through the MCP clang tools as described in the agent instructions.
