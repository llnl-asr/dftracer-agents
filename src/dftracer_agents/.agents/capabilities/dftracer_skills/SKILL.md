---
name: dftracer_skills
description: The dftracer-agents knowledge surface for any harness (Claude Code, OpenCode, Copilot, Codex, Goose). List, search and load skills (whole, one section, or a sibling file), re-render agent/permission/hook copies from their canonical templates, and list/recall/read the git-tracked project memory; validate a skill or memory before persisting. Load at session start and before acting on anything a skill or memory may already cover.
---
Cross-references: [[dftracer_graph]], [[dftracer_lessons]], [[dftracer_privacy]], [[knowledge]], [[lessons]]

# dftracer skills, agents and memory

## Rules
- **Skills first.** Before installing, building, annotating, running, tuning or debugging anything: `skill_search` /
  `graph_query(mode="docs")` / `skill_load` and act on what the system already knows. Never reason from model priors or
  a web search when a `workload-*` / `system-*` / `software-*` / `tools-*` skill covers the task.
- Load narrowly: `graph_query(mode="docs")` + `graph_get_node` for a reference lookup; `skill_load(name,
  section="<heading>")` when the section is known (exact, then prefix, then substring match on the header); a whole
  skill only for a first-time task. `file=` loads a sibling file of the skill (e.g. a lessons log); `related_skills`
  lists the `[[links]]` in what was returned, names only.
- `skill_load` accepts several comma-separated names; names match the frontmatter `name` or the directory name.
- Agents: the canonical definitions are harness-neutral YAML templates under `src/dftracer_agents/.agents/agents/*.yaml`
  (shared prose in `common-sections.yaml`); `.claude/agents/*.md`, `.opencode/agents/*.md`, `.github/agents/*.agent.md`,
  `.codex/agents/*.toml` are generated. After ANY confirmed edit to a template call `agents_sync()`: one-way and
  lossless, never merge rendered copies back; surface its conflicts.
- Permissions: canonical policy `src/dftracer_agents/.agents/workspace/permissions.yaml`; `permissions_sync()` renders
  `.claude/settings.json` permissions and `.opencode/opencode.jsonc` permission (Copilot has only the per-agent tools
  allowlist; Codex takes no sandbox settings from a repo file).
- Hooks: `hooks_sync(target_root)` renders the prompt-injection hook script and each harness's wiring (Claude
  UserPromptSubmit, Codex hooks.json, OpenCode chat.message plugin) into the package and the target project.
- Memory: git-tracked, one markdown file per fact under `src/dftracer_agents/.agents/workspace/memory/`, indexed by
  `MEMORY.md`, identical in every harness. `memory_list` at session start; `memory_recall(query, k, types)` ranks by
  name, description and body and walks one hop of `[[wikilinks]]`; `memory_read(name)` for full text. Writing memory is
  `memory_write` (the store's `knowledge` capability holds that tool name; dftracer-agents' own `memory_write`
  anonymizes, rejects type=user, updates MEMORY.md and marks the graph dirty).
- Generic vs specific: a generic rule reaches all agents through a shared section in `common-sections.yaml` included
  by every template, then `agents_sync`; a specific one goes to its workload/system/software/tools skill.

## Constraints
- Skills, agents, memory are git-tracked and shipped: redact before persisting, never a `type: user` memory
  ([[dftracer_privacy]]); `dftracer_skills_check` must pass before persisting.
- Self-learning edits go through the confirmation gate; agents never self-write skills, agent definitions or tools.
- The skills directory is resolved at call time (dev checkout `.agents/skills` first, then the package's), so new or
  edited skills are visible without a restart.

## Corner cases
- `skill_load(section=...)` with no match returns `available_sections` instead of guessing; a short query like
  "CRITICAL" prefers an exact or prefix header match over a header that merely contains the word.
- Sibling files have no frontmatter and are returned as-is; `section` still applies.
- `agents_sync` returns `status: partial` with `conflicts` when a rendered copy was edited by hand: re-apply the edit
  to the template, never to the copy.
- `hooks_sync`: Copilot has no hook mechanism (`not_applicable`); its tool calls are covered by the MCP context middleware.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
`dftracer_skills_check` failures: `frontmatter` / `name` / `description` -> fix the header (name = directory);
`sections` -> structure the body in `## ` sections; `links` -> point `[[...]]` at an existing skill (skill_list) or
create it; `memory_type` -> project|feedback|reference; `privacy` -> redact with the placeholders. Then check again.
`skill_list` error "No .agents/skills directory found" -> the package or checkout lacks its skills: reinstall/bootstrap.

## Self-heal
- A tool is wrong: fix `mcp_tools/tools/dftracer/skills_service.py` (dftracer-agents) or `capability_update` this
  capability; template/rendering problems are fixed in `dftracer_agents/agents`, `permissions`, `hooks` modules.

## Self-learn
- What is explored here becomes a capability: knowledge that keeps being loaded from a dftracer skill for a workload,
  system, library or tool becomes (or extends) its `workload_` / `system_` / `software_` / `tools_` capability; see
  capability_builder.

## What works
- Session start: `memory_list()` -> `memory_recall("<task>")` -> `skill_search("<task>")` ->
  `skill_load(name, section=...)`.
- After a confirmed template edit: `agents_sync()` and report its summary (dftracer-report finalization step 2).
