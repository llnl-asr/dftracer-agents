---
name: bug-diagnoser-zero-observations-checkpoint
description: RESOLVED: the "0 observations" diagnose() bug was a zombie MCP server process running since a prior day, ignoring every reinstall/restart
metadata:
  type: feedback
---

**Canonical home:** see the `dftracer-diagnoser` skill (`pitfalls.md`, the second
"0 observations" failure mode entry — this file's full content is now persisted there).

RESOLVED root cause for the previously-open `diagnose()` "0 metric observations" discrepancy (MCP call returned 0 findings while the identical standalone `Diagnoser().diagnose_checkpoint()` call in the same venv returned 15 real findings).

**Root cause:** an MCP server process (`dftracer-mcp-server ... --reload` and its `--_child-run` child) had been running continuously for over a day, started before any of the session's `dfdiagnoser`/`dftracer-analyzer` reinstalls. `pip install --force-reinstall` into the shared venv does NOT get picked up by an already-running Python process — the old module is still cached in memory. Multiple "restarts" reported by the user did not actually replace this process (it kept the same PID and start timestamp across supposed restarts) — likely because a different/newer server instance was started on a different port while the old one kept running and answering requests.

**Why the symptom looked like a code bug:** the exact same function (`_diagnose_via_api` in `dfdiagnoser_service.py`), called directly in the same venv/interpreter outside the MCP server, worked correctly every time — proving the tool code was correct and the discrepancy was purely process/environment staleness, not a logic bug.

**Fix:** `ps -o pid,lstart,cmd -p <pid>` to check the ACTUAL start time of the MCP server process(es) — don't trust "I restarted it" at face value if the symptom persists after a reported restart. If the start time predates a relevant `pip install`, kill that exact PID (not just any process matching a grep) and start a fresh one; verify with `curl` that something is actually listening on the port before retrying the tool call. Watch for multiple stale server instances accumulating on different ports over a long-running session — killed a second, completely unrelated stale instance (from 11 days earlier, port 5000) found by the same `ps -o lstart` check before finding the real culprit.

**How to apply:** any time an MCP tool's behavior doesn't reflect a just-installed package change, verify the server process's actual start timestamp before assuming the code itself has a bug.
