---
name: dftracer-project-router
description: Project-level router for dftracer stage subagents, model selection, and tool delegation.
---

## Steps

1. Load the sibling files in this skill directory before deciding anything else.
2. Route each pipeline stage to the narrowest subagent that can finish it.
3. Prefer MCP tools over manual inspection when a tool already exists.
4. Escalate to a larger model only when the stage needs cross-step reasoning.

## Included Context

- Load `rules.md`
- Load `permissions.md`
- Load `pitfalls.md`
- Load `lessons.md`

## Last Step

Before stopping, update the sibling lesson files with any new routing issue,
tool failure, or model-selection insight discovered during the session.
