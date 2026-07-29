---
name: feedback-confirm-before-skill-updates
description: SUPERSEDED 2026-07-29: record learnings into skills/agents/tools IMMEDIATELY as you verify them, then review the list with the user at the end of the report
metadata:
  type: feedback
---

**This reverses the earlier "propose, don't persist" rule.** The old guidance said to
propose skill/agent/tool updates in the final summary and wait for user confirmation
before writing anything. The user has explicitly replaced that:

> "whatever you learn always save it right away into the correct skill/mcp/agent. At the
> end of report you can ask me what you learnt was good or we need any changes."
> "change the skill and agent instructions to do this"

**The rule now:**
1. **Write immediately.** The moment you VERIFY something non-obvious, persist it — do
   not batch it, do not defer to the end of the run, do not wait for permission.
2. **Route by kind:**
   - fact / corner case / knowledge -> the matching skill (`workload-*`, `system-*`,
     `software-*`, `tools-*`)
   - something that changes how an agent should behave -> that agent's YAML template
     under `src/dftracer_agents/.agents/agents/*.yaml`, then run `agents_sync`
   - generic deterministic logic -> an MCP tool under `src/dftracer_agents/mcp_tools/`
     (flag if a server restart is needed)
   - cross-session state/guidance -> `memory_write`
3. **Only record what you actually verified**, with the output that proves it. Label
   still-unconfirmed diagnoses as hypotheses. A wrong lesson in a shared skill is worse
   than no lesson — this is the one guard the old rule was protecting, and it is kept.
4. **Everything persisted stays anonymous** (Pipeline Policy rule 9).
5. **Then review at the end.** List every skill / agent / tool / memory touched and the
   one-line lesson each carries, and ask the user whether it is right or needs changing.
   Review happens AFTER the write — corrections are cheap, lost lessons are not.

**Why:** an agent that is interrupted, killed, or that simply forgets to mention a
finding loses the lesson entirely. Immediate persistence makes the learning durable;
the end-of-session review still catches anything wrong.

**Where this is encoded:** the `self-learning-confirmation-gate` section of
`src/dftracer_agents/.agents/agents/common-sections.yaml` (retitled
"Self-learning: record immediately, review at the end"), plus the per-agent
"RECORD ... IMMEDIATELY" line in 14 agent templates. Re-render with `agents_sync`.

Related: [[feedback_pipeline_selflearning]], [[feedback-working-style]],
[[feedback-privacy-anonymous]].
