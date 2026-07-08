Harness setup
=============

The repository aims to present the same content to three harnesses while only
changing the model backend selection.

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

Shared bootstrap
----------------

The startup bootstrap in ``src/dftracer_agents/bootstrap.py`` materializes the
project-root instruction files and the harness-discoverable skill/agent links.
That keeps the content aligned across all three harnesses while allowing the
model choices to vary by backend. It also turns the root harness files into
symlinks that point at the workspace-owned sources.
