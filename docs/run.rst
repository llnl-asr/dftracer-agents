Running the MCP server
======================

Stdio mode
----------

The default transport is stdio:

.. code-block:: bash

   dftracer-mcp-server

This mode is appropriate for harnesses that launch the server as a child
process and discover tools over stdio.

HTTP mode
---------

The server also supports streamable HTTP:

.. code-block:: bash

   dftracer-mcp-server --transport http --host 0.0.0.0 --port 5000 --path /mcp

The default path is ``/mcp`` and the default port is ``5000``.

Client configuration helpers
----------------------------

To configure Claude Code and Goose for the HTTP endpoint, run:

.. code-block:: bash

   dftracer-configure-mcp --host localhost --port 5000 --path /mcp

Use ``--no-claude`` or ``--no-goose`` to skip one client, and ``--dry-run``
to print the changes without writing any files.
