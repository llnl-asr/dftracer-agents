---
name: mcp-first
description: Never hand-roll what an MCP tool already does. When an MCP tool fails, repair the tool so the next run succeeds — do not work around it with Bash. Load before any pipeline step that calls an MCP tool.
subject: repairing MCP tools instead of working around them
type: skill
---

## Index

| Label | Section | Covers |
| --- | --- | --- |
| `I1` | Instructions | The rule |
| `I2` | Instructions | What to do when a tool fails |
| `E1` | Evidence | What working around it cost |

## I1 — The rule

If an MCP tool exists for a job, it is the only way to do that job.

Doing it by hand with `Bash` is forbidden even when it would be faster, even
when the tool just failed, and even when you can see exactly which shell
command would work. A hand-rolled step leaves nothing behind: the next run
hits the same failure, improvises again, and the system never gets better at
the thing it does most often.

## I2 — What to do when a tool fails

A tool failure is a repair task, not a detour. In order:

1. **Read the error as a defect report.** `Unsupported build tool: unknown`
   is not a fact about this app — it is a gap in the tool's detection.
2. **Fix the tool.** Find its module under
   `src/dftracer_agents/mcp_tools/tools/`, make it handle the case, and say
   in a comment why the case exists.
3. **Re-call the tool.** The run continues through the tool, not around it.
4. **Record it** so the fix survives: a lesson on this skill, and a phronix
   memory entry if it is a fact about the workload rather than the tool.

Only if the tool cannot be repaired within the step's budget: stop and report
the tool defect. Stopping is an acceptable outcome. Silently succeeding by
hand is not — it hides the defect and reports health the system does not have.

Use `Bash` freely for what no tool covers: inspecting the tree, reading logs,
checking whether a fix worked.

## I3 — When a repair takes effect

Immediately. The dftracer MCP server runs over HTTP with `--reload`: it
watches its own source tree and re-execs when a tool file changes. An edit to
an existing tool's body is live within a second or two.

So, having fixed a tool:

1. **Re-call it.** Do not stop, do not report the run blocked, and never ask
   anyone to restart a server — the restart already happened.
2. If the very next call still fails the same way, wait a moment and try once
   more; the re-exec takes a second.
3. Only a NEW or RENAMED tool needs the client to reconnect, because the tool
   list is sent at connection time. Editing an existing tool does not.

There is never a reason to end a turn waiting for a human to restart
something.

## E1 — Evidence

Running pecan_milan on corona, three MCP calls failed in a row:

    session_configure        → "Unsupported build tool: unknown"
    session_build_install    → "Unknown build tool: unknown"
    session_install_dftracer → "dftracer pip install failed"

pecan_milan is raw research Python with no `setup.py` or `pyproject.toml`, so
detection returned `build_tool: unknown` and every builder refused it. The
agent fell back to driving `pip` and `git` by hand.

That path can even produce a working run — and it is still the wrong outcome.
The detector still does not know what a bare-Python workload is, so the next
workload of that shape fails identically, and the pipeline's own measurements
stop describing the pipeline. The fix belongs in the detector.
