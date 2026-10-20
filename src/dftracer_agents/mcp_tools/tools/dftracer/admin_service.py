"""Server self-management tools, so a Claude session can inspect and refresh the MCP server it talks to.

* ``server_status``          - who is serving (stdio / http / zero-downtime worker), how long, whether the package
                               source or the skills/agents content changed since this process started, and, in
                               ``--ha`` mode, the supervisor's generation, workers, last error and request counters.
* ``server_reload``          - zero-downtime rolling restart (``--ha`` mode): the new generation starts, traffic
                               switches when it answers, the old generation drains; the old one keeps serving if the
                               new one fails.
* ``server_refresh_content`` - re-link new/changed skills and agents now (they are read at call time, so this is
                               only about the symlinks Claude Code discovers).

Nothing here is needed in normal operation: the supervisor refreshes by itself.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import time
from typing import Optional

from fastmcp import FastMCP

from ...mcp_service_factory import MCPService

_STARTED = time.time()
_CODE_FP: Optional[str] = None
_CONTENT_FP: Optional[str] = None


def _fingerprints() -> tuple:
    from dftracer_agents.ha import code_fingerprint, content_fingerprint
    return code_fingerprint(), content_fingerprint()


def mark_started() -> None:
    global _CODE_FP, _CONTENT_FP
    _CODE_FP, _CONTENT_FP = _fingerprints()


def _status() -> dict:
    from dftracer_agents.ha import read_state
    global _CODE_FP, _CONTENT_FP
    if _CODE_FP is None:
        mark_started()
    code, content = _fingerprints()
    ha = bool(os.environ.get("DFTRACER_HA_WORKER"))
    out = {"pid": os.getpid(), "host": socket.gethostname(), "uptime_s": round(time.time() - _STARTED, 1),
           "mode": "zero-downtime worker" if ha else "single process (stdio or plain http)",
           "generation": os.environ.get("DFTRACER_HA_GEN"),
           "code_changed_since_start": code != _CODE_FP, "content_changed_since_start": content != _CONTENT_FP}
    st = read_state() if ha else None
    if st:
        out["supervisor"] = st
        out["advice"] = ("source changed: the supervisor performs a rolling restart by itself; "
                         "call server_reload to do it now" if not st["source_current"] else "up to date")
    elif out["code_changed_since_start"]:
        out["advice"] = ("the package code changed after this server started and a single-process server cannot swap "
                         "itself without dropping your connection: restart it (in Claude Code: /mcp reconnect) or run "
                         "`dftracer-mcp-server start --ha` for zero-downtime reloads")
    else:
        out["advice"] = "up to date"
    return out


def _reload() -> dict:
    pid = os.environ.get("DFTRACER_SUPERVISOR_PID")
    if not pid:
        return {"reloaded": False, "reason": "not running under the zero-downtime supervisor; start the daemon with "
                                              "`dftracer-mcp-server start --ha` (then this tool and file changes both trigger "
                                              "rolling restarts), or reconnect the MCP server for a single-process server"}
    os.kill(int(pid), signal.SIGHUP)
    return {"reloaded": True, "note": "rolling restart requested; this worker keeps answering until the new generation "
                                      "is ready - call server_status to watch the generation change"}


def _refresh_content() -> dict:
    from dftracer_agents.ha import default_content_hook
    default_content_hook()
    return {"refreshed": True, "note": "skills and agents re-linked (idempotent)"}


def _register(mcp: FastMCP) -> None:
    @mcp.tool()
    def server_status() -> str:
        """State of this dftracer MCP server: mode, uptime, whether code/content changed since start, and (zero-downtime
        mode) generation, workers, last error and counters. Normally nothing needs refreshing."""
        return json.dumps(_status(), indent=1)

    @mcp.tool()
    def server_reload() -> str:
        """Zero-downtime rolling restart of the MCP server (needs `dftracer-mcp-server start --ha`). Use after editing
        tool code if you do not want to wait for the automatic file watcher."""
        return json.dumps(_reload(), indent=1)

    @mcp.tool()
    def server_refresh_content() -> str:
        """Re-link new or changed skills and agents now (idempotent). Their text is read at call time already."""
        try:
            return json.dumps(_refresh_content(), indent=1)
        except Exception as e:
            return json.dumps({"refreshed": False, "error": f"{type(e).__name__}: {e}"})


class AdminService(MCPService):
    def __init__(self) -> None:
        mark_started()
        self.admin_subservice = FastMCP("DFTracerAdmin")
        _register(self.admin_subservice)

    def execute(self, data: dict) -> Optional[str]:
        return "Use server_status / server_reload / server_refresh_content."

    @property
    def name(self) -> str:
        return "dftracer-admin"
