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
* ``dftracer-install-skills``
* ``dftracer-install-agents``
* ``dftracer-bootstrap-workspace``

Pip install
-----------

For an editable install, use the same command as above. The package exposes
the bundled agent assets under ``src/dftracer_agents/.agents/`` so the startup
bootstrap can install or link them into the harness-discoverable locations.

The repository does not currently require a separate docs-specific dependency
file. ReadTheDocs can install the project with ``pip install -e .`` and build
the Sphinx pages from ``docs/conf.py``.
