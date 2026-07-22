---
name: feedback-never-manual-when-restart-needed
description: "Never hand-write a workaround (regex script, compatibility shim) when the real fix is an MCP tool that needs a server restart — stop and say so instead"
metadata: 
  node_type: memory
  type: feedback
  
---

When an agent needs an MCP tool that's missing, broken, or was just added/fixed
and needs a server restart to take effect, it must STOP and report that
explicitly — never fall back to manual/hand-rolled work (a regex or AST script
reproducing the tool's job, a compatibility shim papering over a real API
mismatch, hand-editing files the tool should generate).

**Why:** this is exactly how the flux-fiction session's brace-corruption and
`dftracer.logger` API-drift bugs got introduced — an earlier agent, lacking
`python_annotate_file`, wrote an ad hoc decorator-insertion script that used a
stale API pattern; a later agent, hitting a real `pydftracer` gap, wrote a
silent no-op compatibility shim instead of surfacing the mismatch. Both
"worked" well enough to look like progress while quietly producing wrong or
inert output (annotated files that never actually emitted trace events).

**How to apply:** codified as the `never-manual-when-a-restart-is-needed`
shared section in `common-sections.yaml`, wired into all ~28 agents that
already include `self-learning-confirmation-gate`. When dispatching or
resuming an agent after a tool/skill fix, explicitly tell it to wait rather
than work around a gap — a short pause for a restart is always cheaper than
finding and undoing a manual workaround later.
