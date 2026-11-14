"""dftracer_admin: let a session inspect and refresh the dftracer MCP server it talks to. server_status (mode, uptime,
whether code or skills/agents changed since start, supervisor state in zero-downtime mode), server_reload (rolling
restart under the --ha supervisor) and server_refresh_content (re-link skills and agents); a validator rules on a
status report: is the server serving current code, and if not, what to do."""
import json
from typing import Any, Dict, List

from dftracer_agents.mcp_tools.tools.dftracer.admin_service import _refresh_content, _reload, _status

_NEED = ("pid", "mode", "uptime_s", "code_changed_since_start", "content_changed_since_start", "advice")


def _admin_verdict(st: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def add(name, ok, detail, fix):
        checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})

    miss = [k for k in _NEED if k not in st]
    add("shape", not miss, f"missing keys: {miss}" if miss else "status complete", "pass the JSON server_status returned, unmodified")
    ha = "zero-downtime" in str(st.get("mode", ""))
    sup = st.get("supervisor") or {}
    if ha:
        current = bool(sup.get("source_current", not st.get("code_changed_since_start")))
        add("code_current", current, "supervisor serves the current source" if current else "the source changed and the new generation is not serving yet",
            "server_reload() now (rolling restart), then server_status until the generation changes")
        err = sup.get("last_error")
        add("supervisor_error", not err, f"last_error: {err}" if err else "no supervisor error",
            "read the error; the old generation keeps serving: fix the code that failed to start and server_reload again")
    else:
        changed = bool(st.get("code_changed_since_start"))
        add("code_current", not changed, "code unchanged since start" if not changed else "the package code changed after this server started",
            "a single-process server cannot swap itself: reconnect the MCP server (Claude Code: /mcp reconnect) or run "
            "`dftracer-mcp-server start --ha` for zero-downtime reloads")
    add("content_current", not st.get("content_changed_since_start") or ha,
        "skills/agents unchanged since start" if not st.get("content_changed_since_start") else "skills/agents changed since start",
        "server_refresh_content() re-links them (their text is read at call time already)")
    return {"status": "pass" if all(c["ok"] for c in checks) else "fail", "checks": checks}


def register(mcp):
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

    @mcp.tool()
    def dftracer_admin_check(status_json: str) -> dict:
        """Validate a server_status report deterministically: pass the JSON text server_status returned. Checks: the
        report is complete (pid, mode, uptime_s, code/content changed flags, advice); the server serves current code (a
        single-process server whose code changed must be reconnected; under the zero-downtime supervisor the source must
        be current and there must be no last_error); skills/agents content is current. Use after editing tool code or
        skills, before relying on the change. Returns {status: pass|fail|inconclusive, checks: [{check, ok, detail, fix}]}."""
        try:
            st = json.loads(status_json)
        except (TypeError, ValueError) as e:
            return {"status": "inconclusive", "checks": [{"check": "json", "ok": False, "detail": f"not JSON: {e}"[:200],
                                                          "fix": "pass server_status()'s output text unchanged"}]}
        if not isinstance(st, dict):
            return {"status": "inconclusive", "checks": [{"check": "json", "ok": False, "detail": "not an object",
                                                          "fix": "pass server_status()'s output text unchanged"}]}
        return _admin_verdict(st)

    @mcp.tool()
    def dftracer_admin_selftest() -> dict:
        """Self-test dftracer_admin_check on status fixtures: an up-to-date single-process server passes; one whose code
        changed fails code_current; a zero-downtime worker with a stale source or a supervisor last_error fails; a
        truncated report fails shape. Use to check the validator. Returns {status: pass|fail, detail}."""
        base = {"pid": 1, "host": "h", "uptime_s": 5.0, "mode": "single process (stdio or plain http)", "generation": None,
                "code_changed_since_start": False, "content_changed_since_start": False, "advice": "up to date"}
        ha = {**base, "mode": "zero-downtime worker", "generation": "3",
              "supervisor": {"source_current": True, "last_error": None}}
        failed = lambda d: [c["check"] for c in dftracer_admin_check(json.dumps(d))["checks"] if not c["ok"]]
        r = {
            "fresh": failed(base),
            "stale_single": failed({**base, "code_changed_since_start": True}),
            "ha_ok": failed(ha),
            "ha_stale": failed({**ha, "supervisor": {"source_current": False, "last_error": None}}),
            "ha_error": failed({**ha, "supervisor": {"source_current": True, "last_error": "ImportError: x"}}),
            "short": failed({"pid": 1}),
        }
        ok = (r["fresh"] == [] and r["stale_single"] == ["code_current"] and r["ha_ok"] == [] and r["ha_stale"] == ["code_current"]
              and r["ha_error"] == ["supervisor_error"] and "shape" in r["short"])
        return {"status": "pass" if ok else "fail", "detail": json.dumps(r)}
