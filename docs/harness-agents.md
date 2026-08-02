# One Agent Template, Four Harnesses

The dftracer pipeline agents are defined **once**, as harness-neutral YAML
templates, and **rendered** into the on-disk dialect of each supported harness
(Claude Code, OpenCode, GitHub Copilot, Codex CLI). Git tracks only the
templates; the rendered files are disposable build artifacts.

```
src/dftracer_agents/.agents/agents/          ← canonical, git-tracked
├── common-sections.yaml                     ← shared prose blocks (16 today)
├── dftracer-analyzer.yaml
├── dftracer-annotator.yaml
└── ... (20 agent templates)

            │  render (ensure_agents_setup / agents_sync)
            ▼
.claude/agents/<name>.md                     ← Claude Code      (gitignored)
.opencode/agents/<name>.md                   ← OpenCode         (gitignored)
.github/agents/<name>.agent.md               ← GitHub Copilot   (gitignored)
.codex/agents/<name>.toml                    ← Codex CLI        (gitignored)
```

## Template schema

```yaml
name: dftracer-analyzer
description: >-
  Pipeline stage 5. Runs dfanalyzer over compacted traces ...
model_level: level_3        # semantic tier; resolved per harness (see Models)
effort: low                 # Claude-only hint; other harnesses ignore it
isolation: worktree         # Claude-only hint
tools:                      # neutral names (Claude Code's names are the
  - Read                    # neutral form; converters reshape them)
  - Bash
  - mcp__dftracer__analyze
skills:
  - dftracer-context-economy
  - dftracer-trace-utils
sections:                   # ordered prompt body
  - title: Load your plan section first
    body: |
      The pipeline planner has written a detailed plan ...
  - include: self-learning-feed-lessons-back-into-skills   # shared block
  - include: step-profiling
```

Required keys: `name`, `description`, `model_level`, `sections`.
Optional: `effort`, `isolation`, `tools`, `skills`.

### Shared sections (`common-sections.yaml`)

Prose that applies to every agent (self-learning rules, step profiling,
context economy, privacy redaction, artifacts logging, …) lives once in
`common-sections.yaml` as `sections: {<slug>: {title, body}}` and is pulled
into a template with `- include: <slug>`. Editing a shared block updates all
agents that include it on the next sync. A template can still override by
writing its own inline `{title, body}` section instead of the include.

## Converters

`src/dftracer_agents/agent_templates.py` holds one converter per harness.
All four share the same body rendering (`## <title>` markdown, sections in
order) and differ only in frontmatter — except Codex, which is TOML, not
markdown+frontmatter, so it has no shared body at all (`developer_instructions`
carries the whole rendered body as a single TOML string):

| field | claude (`.claude/agents/*.md`) | opencode (`.opencode/agents/*.md`) | copilot (`.github/agents/*.agent.md`) | codex (`.codex/agents/*.toml`) |
|---|---|---|---|---|
| model | class alias (`haiku`/`sonnet`/`opus`) | `provider/model-id` (e.g. `ollama/qwen3.5:32b`) | bare model id | bare model id |
| permission/tools | comma-separated string | `permission:` map `{"*": "deny", <tool>: "allow"}` (allowlist) | YAML list | none — Codex has no per-tool concept; see `sandbox_mode` below |
| MCP tool names | `mcp__dftracer__analyze` | `dftracer_analyze` | `dftracer/<tool>` | n/a (MCP server registered project-wide in `.codex/config.toml`, not per-agent) |
| built-ins | `Read, Bash, Edit, Grep` | `read, bash, edit, grep` | `read, shell, edit, search` | n/a |
| skills | `skills:` frontmatter key | injected "Load your skills first" body section calling `skill_load(...)` | same injected section | same injected section (in `developer_instructions`) |
| extras | `effort`, `isolation`, `model_level` kept | `mode: subagent` added | `name` kept | `effort` → `model_reasoning_effort`; fixed `sandbox_mode = "workspace-write"` |

Every rendered file starts with a generation marker comment naming the
harness and pointing back at the template. The installer refuses to overwrite
any file that lacks the marker (reported as a conflict), so a hand-written
agent with a colliding name is never clobbered.

Caveats:

- OpenCode's `permission:` frontmatter key is the v1.1.1 format (replacing the
  deprecated `tools:` field). We emit
  `permission: {"*": "deny", <tool>: "allow"}` with string allow/deny values.
  The old `tools:` field with boolean values is no longer generated.
- Copilot tool names (`shell`, `search`, `dftracer/<tool>`) follow the
  custom-agents reference; if an agent runs with missing tools in Copilot,
  adjust `_COPILOT_BUILTIN` / `_copilot_tool_name` in `agent_templates.py`.
- Codex's execution permission model is `sandbox_mode` (read-only /
  workspace-write / danger-full-access) + `approval_policy`, not discrete tool
  names, and — deliberately, as an anti supply-chain-attack measure — neither
  key is honored when set in a repo-tracked `.codex/config.toml`. We fix
  `sandbox_mode = "workspace-write"` per rendered agent as a best-effort
  default; see `docs/harnesses.rst`'s "Permission tiers" section for the full
  story and why the workspace/project-root/outside tiers can't be fully
  reproduced for Codex from a git-tracked file the way they can for Claude and
  OpenCode.

## Models: `model_level` → concrete model

Templates never name a model. They name a semantic tier (`level_1`…`level_4`)
defined in `.agents/workspace/models.yaml`; the per-harness resolution comes
from `.agents/workspace/active-models.json`, managed by
`dftracer-configure-harness` (see `harness_models.py`):

- **claude** → the level's model *class* (`haiku`/`sonnet`/`opus`), which
  Claude Code accepts as an alias, keeping rendered files stable across model
  version bumps.
- **opencode** → the configured provider's model id, prefixed
  (`ollama/…`, `anthropic/…`, `github-copilot/…`).
- **copilot** → the configured model id as-is.

Changing `active-models.json` (e.g. via `dftracer-configure-harness
--interactive`) changes rendered output; run a sync afterwards.

## Syncing

Rendering happens automatically on MCP server startup
(`ensure_agents_setup()`), and on demand:

- **MCP tool** (preferred, per Tool-First rule): `agents_sync()` — re-renders
  all three harnesses, returns what changed and any conflicts.
- **CLI**: `dftracer-install-agents --target cwd [--harness claude|opencode|copilot]`
- **Python**: `from dftracer_agents.agents import sync_agents; sync_agents()`

Sync is strictly one-way (template → copies). There is no merge-back: if you
edit a rendered file the next sync overwrites it (the marker identifies it as
ours). This is deliberate — it is what keeps the template the single tracked
source of truth.

## Self-learning workflow (Pipeline Policy rule 10)

When an agent learns something that changes how it should behave:

1. Edit the agent's YAML template (`src/dftracer_agents/.agents/agents/<name>.yaml`)
   — usually by editing/adding a section body. If the lesson applies to every
   agent, edit the shared block in `common-sections.yaml` instead.
2. Call the `agents_sync` MCP tool so all three harnesses pick it up.
3. Ask the user to reload the harness (agents are read at session start).
4. The `dftracer-privacy-guard` step scans the templates (git-tracked) as
   usual; rendered copies are gitignored and never scanned.

## Adding a new agent

Create `src/dftracer_agents/.agents/agents/<name>.yaml` following the schema,
reuse the shared blocks via `include:`, then run `agents_sync`. No converter
changes are needed unless the agent uses a tool name the harness maps don't
cover yet.
