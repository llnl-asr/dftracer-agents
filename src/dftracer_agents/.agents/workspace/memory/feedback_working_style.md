---
name: feedback-working-style
description: "User's preferred working style: autonomous, continues mid-session pipelines without recapping, fixes MCP tools during optimization runs"
metadata: 
  node_type: memory
  type: feedback
---

**Canonical home (partial — see note):** the allocation-ID-hotswap sub-part below is
covered by the `flux-alloc` skill ("Allocations: ASK the user first" section handles
mid-session JOBID changes); the "fix the MCP tool, not a workaround" sub-part is
covered by `dftracer-privacy-guard`/`dftracer-lessons` (tool-first, fix-the-tool
rules). The autonomous-continuation preference (first paragraph below) has no
project-skill home — it is a personal interaction-style preference, not a technical
lesson, so it stays as personal-memory-only text.

User runs long multi-step optimization pipelines and expects Claude to continue where the session left off without asking questions or summarizing context.

**Why:** Sessions are long-running (hours); the user provides new allocation IDs mid-session and expects immediate continuation.

**How to apply:** When resuming a session from a summary, pick up at the last pending task immediately. Do not greet, recap, or ask "should I continue?" — just continue.

---

When the user says "fix the MCP tool as well" during an optimization run, they mean fix the underlying service Python file, not a workaround. Fix the root cause in the source file directly.

**Why:** Workarounds (running dfanalyzer manually via bash) are acceptable temporarily but the user expects the MCP tool itself to work correctly going forward.

**How to apply:** When an MCP tool generates wrong output, find and edit the relevant service file (e.g., `dfanalyzer_service.py`) to fix the root cause.

---

The user provides allocation IDs when the previous one is about to expire, e.g., "the allocation will end soon use this <ID>". This is a signal to immediately switch to using the new allocation ID for all subsequent flux proxy commands.

**Why:** Tuolumne allocations have time limits; the user manages them manually.

**How to apply:** Replace the previous JOBID with the new one in all subsequent flux run / flux proxy commands.
