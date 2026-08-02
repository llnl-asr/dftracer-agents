---
name: project_package_restructure
description: dftracer-agents restructured to real src/ Python package layout (no more hyphenated dirs / sys.modules hack); harness-specific skill+MCP discovery installer added
metadata: 
  node_type: memory
  type: project
---

**Canonical home:** no dedicated skill — this is pure repo-engineering history (src
layout, `skills.py` cross-harness installer). The current layout is simply the
repo's own structure, not documented in a skill; kept here as historical record only.

As of 2026-07-06, dftracer-agents was restructured to be genuinely pip/uv-installable, superseding [[project_skills_reorg]]:

- **Real src-layout package.** `dftracer-agents/` and `mcp-tools/` (hyphenated, not valid Python identifiers) were git-mv'd to `src/dftracer_agents/` and `src/dftracer_agents/mcp_tools/`. `.agents/` (skills) moved physically to `src/dftracer_agents/.agents/` — it is package data now, not a repo-root symlink (the user explicitly said they don't want `.agents` at the project root anymore).
- **Deleted the sys.modules bootstrap hack** in `mcp_server.py` (`_is_package_installed`, `_bootstrap_package_context`, `_load_module`, ~120 lines). It existed to work around the hyphenated dirs; confirmed via testing that plain `import dftracer_agents.mcp_tools.tools.X.Y` already worked fine even before the rename (setuptools' package-dir mapping handled the hyphen translation) — the hack was solving an already-solved problem. `mcp_server.py` now does plain absolute imports.
- **`pyproject.toml`** rewritten to standard `[tool.setuptools.packages.find] where = ["src"]` (auto-discovery, no manual package-dir map). This also fixed a real bug: the old manual package list was missing `dftracer_agents.mcp_tools.tools.system` entirely.
- **`setup.py` deleted** — its only job (symlinking `.agents/` into the package at build time) is now unnecessary since `.agents/` physically lives inside `src/dftracer_agents/`.
- **Orphaned legacy file removed from the package:** `tools/dftracer_session_service.py` (2761 lines, superseded by `tools/dftracer/dftracer_service.py` + the `session/` package, never imported anywhere) moved to `legacy/dftracer_session_service.py.orphaned` at repo root (kept, not deleted).
- **`.gitignore` cleaned:** collapsed scattered stale egg-info entries to `*.egg-info/`; removed a dead `dftracer-agents/.agents` ignore rule left over from the old symlink hack.
- **Cross-harness discovery, new `dftracer_agents/skills.py`:**
  - `install_skills(target_root)` symlinks (never copies) each bundled skill into `<target_root>/.claude/skills/<name>/` — Claude Code's real native skill-file convention (not the bespoke `.agents/skills` this repo used internally). Merge-safe: a name collision with a pre-existing *unrelated* user skill falls back to a `dftracer-<name>` namespaced link instead of clobbering it; only warns/skips if even that's taken.
  - Goose has no skill-file convention — it's expected to discover dftracer capability via the `skill_list`/`skill_search`/`skill_load` MCP tools directly (decided earlier in [[project_skills_reorg]]). No file installation needed for Goose.
  - `ensure_setup()` is tracked/idempotent via `~/.dftracer-agents/setup_state.json` (keyed by resolved target dir, records the bundled skill-name set so it only re-runs when the package's skill set changes or `force=True`).
  - **`dftracer-mcp-server` now calls `ensure_setup()` automatically on every startup** (per explicit user instruction — "run the setup as part of starting the mcp server... then run the mcp server"), before building/serving. Logs to stderr only (stdio transport uses stdout/stdin for the JSON-RPC protocol itself, so setup output must never touch stdout). `--skip-setup` flag opts out. Default target: CWD if it looks like a project (has `.git` or `pyproject.toml`), else `~`.
  - MCP server *registration* (`.mcp.json` / Claude `settings.json` / Goose `config.yaml`) is deliberately NOT auto-run — that remains the pre-existing explicit `dftracer-configure-mcp` CLI (`mcp_setup.py`, untouched). Auto-modifying harness config files was treated as materially riskier than symlinking skill files (the auto-mode classifier had already blocked a much smaller settings.json edit earlier in this same session without explicit confirmation).
- Verified end-to-end: fresh scratch "project" with only `pyproject.toml`, running `dftracer-mcp-server --service skills` from cold, auto-installed all 28 skills into `.claude/skills/`, then served — all 120 MCP tools build cleanly under `build_server('both')`.

**Feedback embedded in this task:** when a directory rename is blocked by pre-existing stale/deleted files mid-tree (`git mv` refuses if git's index doesn't match the working tree), stage those pre-existing deletions with `git add -u <path>` first — don't work around it by leaving the rename undone. The user explicitly said "no clean up stale things" (i.e. "now clean up") granting permission to finalize the stale deletions as part of this pass rather than tip-toeing around them.
