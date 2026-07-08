---
name: dftracer-system-detect
description: Detect the runtime system, modules, allocation shape, and environment assumptions for dftracer sessions.
---

## Steps

1. Load the sibling files in this skill directory.
2. Detect system facts with the MCP system-detect tool.
3. Capture module, filesystem, and allocation constraints early.
4. Record any new system-specific pitfall immediately.

## Included Context

- Load `rules.md`
- Load `permissions.md`
- Load `pitfalls.md`
- Load `lessons.md`
