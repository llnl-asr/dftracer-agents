---
name: dftracer-annotate-project
description: Project-level annotation router that scopes files and dispatches by language.
---

## Steps

1. Load the sibling files in this skill directory.
2. Scope the target files with MCP discovery tools.
3. Dispatch C, C++, and Python files to the matching file-type subagent.
4. Validate the annotated set and report coverage.

## Included Context

- Load `rules.md`
- Load `permissions.md`
- Load `pitfalls.md`
- Load `lessons.md`

## Last Step

Before stopping, update the sibling lesson files with any new annotation routing
issue, file-type split issue, or tool failure discovered during the session.
