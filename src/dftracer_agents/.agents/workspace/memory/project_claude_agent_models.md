---
name: project_claude_agent_models
description: Claude agent install materializes files and resolves level_N model placeholders to real classes
metadata: 
  node_type: memory
  type: project
---

**Canonical home:** no dedicated skill — the current model-resolution mechanism is
described in CLAUDE.md's "Model Map" section, pointing at
`src/dftracer_agents/.agents/workspace/models.yaml` as the source of truth. This
memory file is historical (the original bug-fix narrative); no skill/agent write-up
duplicates it beyond that pointer.

The bundled dftracer agents under `src/dftracer_agents/.agents/agents/*.md` use shared `model: level_N` placeholders (multi-harness). Claude Code cannot interpret `level_N` — spawning failed with "model may not exist".

Fix (2026-07-07): `dftracer_agents/agents.py` `install_agents` now MATERIALIZES each agent as a real file in `.claude/agents/` (not a symlink) and rewrites `model: level_N` → concrete Claude class from `.agents/workspace/active-models.json` (`level_1→haiku, level_2/3→sonnet, level_4→opus`). A `_GEN_MARKER` line marks our copies; `_is_current` makes idempotency/self-heal content-based. The MCP server's startup `ensure_agents_setup` keeps them in sync.

**Note:** a running Claude session loads agents at start, so edits to agent md files need `ensure_agents_setup(force=True)` AND a session/window reload to take effect; within a live session, spawn with an explicit `model:` override (sonnet/opus/haiku) to bypass stale `level_N`.
