Installation
============

Source install
--------------

The repository is designed to run from source while developing:

.. code-block:: bash

   git clone <this-repo>
   cd dftracer-agents

   python -m venv venv
   source venv/bin/activate

   pip install -e .

This installs the console scripts exposed by ``pyproject.toml``:

* ``dftracer-mcp-server``
* ``dftracer-configure-mcp``
* ``dftracer-configure-env``
* ``dftracer-install-skills``
* ``dftracer-install-agents``
* ``dftracer-bootstrap-workspace``
* ``dftracer-configure-harness``

Pip install
-----------

For an editable install, use the same command as above. The package exposes
the bundled agent assets under ``src/dftracer_agents/.agents/`` so the startup
bootstrap can install or link them into the harness-discoverable locations.

The repository does not currently require a separate docs-specific dependency
file. ReadTheDocs can install the project with ``pip install -e .`` and build
the Sphinx pages from ``docs/conf.py``.

.. _configuring-api-keys:

Configuring API keys and auth tokens
-------------------------------------

Run ``dftracer-configure-env`` once after installing to create ``.env`` at the
project root and fill in the values it needs:

.. code-block:: bash

   dftracer-configure-env                    # interactive prompts
   dftracer-configure-env --non-interactive   # only auto-generate tokens;
                                               # seed API keys from the
                                               # environment when present

It resolves the project root the same way ``dftracer_agents_stack`` does
(walks up from the current directory for ``workspaces/``, then ``.git`` /
``pyproject.toml``), so it is safe to run from anywhere inside a checkout.
Re-running is idempotent — it only fills in blanks and never overwrites a
value you already set.

It manages three groups of values in ``.env``:

* **Auth tokens** — ``DFTRACER_MCP_TOKEN`` / ``DFTRACER_COLLECTOR_TOKEN``,
  auto-generated with ``secrets.token_hex(32)`` if blank. These gate the
  Docker Compose stack's MCP/OTLP endpoints behind Caddy — see
  :doc:`docker`.
* **Academic paper search keys** — ``SEMANTIC_SCHOLAR_API_KEY``,
  ``CORE_API_KEY``, ``OPENALEX_MAILTO``. All optional: every source in the
  ``AcademicPapers`` tool group (see :doc:`tools`) falls back to anonymous,
  client-side rate-limited access when these are blank, so leaving them
  empty is a normal, fully supported configuration.
* **Stack ports** — ``MCP_PORT`` / ``COLLECTOR_PORT`` / ``MLFLOW_PORT``. Each
  is probed with a real bind on ``127.0.0.1``: the launcher's default
  (5000/4318/5001) is used if free, otherwise the next free port is picked
  automatically — and a port already claimed by one of the other two
  services in the same run is skipped, so no two services collide with each
  other either. Left blank only if no free port is found in range. Shared
  HPC login nodes commonly have the defaults already taken by another user's
  session, which is exactly the ``dftracer_agents_stack start`` →
  ``REFUSING to start ... is held by a process`` failure this avoids.

``.env`` is only ever read by two things: ``docker compose`` (automatically),
and ``dftracer_agents_stack`` in bare-metal/local mode, which sources it
before starting the daemons so the values above reach the running processes
either way.
