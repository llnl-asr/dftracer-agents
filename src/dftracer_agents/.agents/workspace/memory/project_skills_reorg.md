---
name: project_skills_reorg
description: "Skills reorg — .agents/skills is canonical, MCP skill_list/search/load tools, prose Permissions sections, .claude/commands removed, session structure validate/reorganize tools"
metadata: 
  node_type: memory
  type: project
---

**Canonical home:** no dedicated skill — superseded in part by
`project_package_restructure` (the `.agents/skills` canonical layout described here
is now `src/dftracer_agents/.agents/skills/`). Session-structure enforcement details
live in the `dftracer-cheatsheet` skill's S0 section it mentions below. Otherwise
pure repo-engineering history.

As of 2026-07-05, dftracer-agents skills and session-structure enforcement were consolidated:

- **Canonical source:** `.agents/skills/<name>/SKILL.md` (28 skills). `setup.py` symlinks this into the package at build; nested `dftracer-agents/agents/.agents/skills` and `build/` copies are derived, not sources.
- **MCP skill-loading tools:** `dftracer-agents/mcp-tools/tools/dftracer/skills_service.py` exposes `skill_list`, `skill_search`, `skill_load` so any harness (Goose/opencode/custom), not just Claude Code, can pull skills into context. Wired into `mcp_server.py` via `_build_skills_server()`, added to `--service skills` and `both`.
- **`.claude/commands` removed:** the 7 command files were merged into their same-named skills ("keep richest" per pair) then deleted. `settings.json` allow list updated from `.claude/commands/*` to `.agents/skills/*`.
- **Permissions convention:** moved skills end with a prose `## Permissions` section (MCP tools, Bash patterns, Write scope). Documentation only.
- **Session directory structure enforcement (new):** canonical layout is `workspaces/<app>/<run_id>/{baseline,annotated,opt<n>}/{source,scripts,traces/{raw,compact},patches}` + `artifacts/,tmp/,dataset/,session_report.md,session.json`, built by `_init_structure`/`session_init_structure` in `dftracer-agents/mcp-tools/tools/session/session_tools.py`. An older "legacy flat" layout (`ws/source`, `ws/build_ann`, `ws/traces`, `ws/venv`, etc., used by `pipeline_tools.py`/`install.py`/`annotation.py`) coexists and is a known drift source.
  - Added two new MCP tools in `session_tools.py`: `session_validate_structure(run_id)` (read-only, flags missing canonical paths / legacy drift / unexpected top-level entries, and refreshes `session.json["paths"]`) and `session_reorganize_structure(run_id, dry_run=True)` (quarantines legacy paths into `artifacts/legacy/<name>/`, never deletes, never guesses which run legacy data belongs to).
  - `session.json["paths"]` is now the persisted single source of truth for exact paths — written by `session_init_structure`, `session_validate_structure`, and `session_reorganize_structure`.
  - Added a new strict-rule section **S0** at the top of `dftracer-cheatsheet` SKILL.md (read-first skill): never hand-build session paths; call `session_validate_structure` before annotation and before/after every optimization iteration.
  - Wired validate/reorganize calls into `dftracer-pipeline` (Step 2f before annotation, Step 8a-0 before each opt iteration), `dftracer-ml-annotate` (Step 2e before annotation), and `dftracer-io-optimization` (before/after each L1/L2/L3 iteration).
  - Found and fixed real drift in `dftracer-ml-annotate` STEP 8/11: it documented the WRONG legacy layout (`<WS>/traces/<label>/raw`) instead of canonical (`<WS>/<label>/traces/raw`) — corrected to use `session_get_run_paths` instead of hand-built strings.

Template direction (agreed earlier): wrap-and-preserve — add structure without deleting existing detailed content. Full 5-section template rollout across all 28 skills is still future work; only the skills touched by this session and the 7 command-derived ones have been updated.
