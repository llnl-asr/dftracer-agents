Harness setup
=============

The repository aims to present the same content to four harnesses (Claude
Code, OpenCode, GitHub Copilot, and OpenAI Codex CLI) while only changing the
model backend selection.

The canonical harness instructions and config files live under
``src/dftracer_agents/.agents/workspace/``. The project-root files are
bootstrapped as symlinks so each harness sees the same content without keeping
duplicate copies in the top level of the repository.

Claude Code
-----------

Claude Code uses the project ``.claude`` directory and the root ``CLAUDE.md``
instructions file.

The root ``.claude/settings.json`` file is linked from the source workspace
config so the harness settings stay source-controlled with the package.

The startup path installs skills and agents into:

* ``.claude/skills/``
* ``.claude/agents/``

The helper command for configuring the MCP server is:

.. code-block:: bash

   dftracer-configure-mcp

OpenCode
--------

OpenCode loads project instructions from ``AGENTS.md`` and can also read the
workspace-level model matrix in ``src/dftracer_agents/.agents/workspace/models.yaml``.

The repository includes an OpenCode config file at ``.opencode/opencode.jsonc``
that references the shared instructions and MCP server.

That config is linked from the source workspace copy rather than maintained as
an independent top-level file.

OpenCode discovers the shared skills and agents through the linked directories:

* ``.opencode/skills/``
* ``.opencode/agents/``

Copilot / VS Code
-----------------

Copilot uses ``copilot-instructions.md`` together with the workspace MCP
configuration at ``.vscode/mcp.json``.

The bootstrap step writes the same shared instruction content into
``copilot-instructions.md`` so Copilot sees the same project guidance as the
other harnesses.

The Copilot instruction file and ``.vscode/mcp.json`` are both linked from the
source workspace so the project root stays thin.

Codex CLI
---------

Codex CLI reads project instructions from ``AGENTS.md`` (same file OpenCode
reads), custom subagents from ``.codex/agents/*.toml`` (TOML, not markdown —
see ``docs/harness-agents.md``), and skills directly from ``.agents/skills/``
at the repo root — the same path this repo already symlinks for every
install target, so Codex needs no extra skill wiring at all.

The MCP server is registered in ``.codex/config.toml`` under
``[mcp_servers.dftracer]``, linked from the source workspace copy like the
other harnesses' configs.

**Asymmetry to know about**: Codex deliberately ignores ``sandbox_mode`` /
``approval_policy`` when set in a repo-tracked ``.codex/config.toml`` (a
cloned repo must not be able to loosen its own sandbox), and only loads
``.codex/`` config at all for *trusted* projects. Neither of those is
something this repo can set for you — ``dftracer-configure-mcp`` prints a
one-line advisory with the exact ``~/.codex/config.toml`` snippet to add
yourself.

The helper command for configuring the MCP server is the same one used for
the other three harnesses:

.. code-block:: bash

   dftracer-configure-mcp

Permission tiers
-----------------

The workspace/project-root/outside permission tiers documented in the
project's ``CLAUDE.md`` (Permission Tiers table) come from one canonical
policy file, ``src/dftracer_agents/.agents/workspace/permissions.yaml``,
rendered per harness by ``src/dftracer_agents/permissions.py``:

* **Claude Code** — full fidelity: ``.claude/settings.json``'s
  ``permissions.allow`` / ``permissions.deny`` arrays.
* **OpenCode** — full fidelity: ``.opencode/opencode.jsonc``'s top-level
  ``permission`` glob map.
* **Copilot** — not applicable; custom agents only support a per-agent
  ``tools:`` allowlist (already rendered by ``agents_sync``), not
  path/command-scoped permissions.
* **Codex** — not settable from a repo-tracked file (see the asymmetry
  above); ``render_codex`` sets a fixed ``sandbox_mode = "workspace-write"``
  per agent instead.

Sync with ``dftracer-sync-permissions`` or the ``permissions_sync`` MCP tool
after editing ``permissions.yaml``.

Shared bootstrap
----------------

The startup bootstrap in ``src/dftracer_agents/bootstrap.py`` materializes the
project-root instruction files and the harness-discoverable skill/agent links.
That keeps the content aligned across all four harnesses while allowing the
model choices to vary by backend. It also turns the root harness files into
symlinks that point at the workspace-owned sources.
