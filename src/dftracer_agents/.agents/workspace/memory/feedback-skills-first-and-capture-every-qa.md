---
name: feedback-skills-first-and-capture-every-qa
description: Standing rule: search skills BEFORE acting, record every user Q&A as a lesson immediately, and propagate generic rules to ALL skills and agents
metadata:
  type: feedback
---

Three-part standing rule the user gave on 2026-07-28/29, now enforced as the shared agent
section `skills-first-and-capture-every-qa` (in `agents/common-sections.yaml`, `- include:`d
by all 29 agent templates) and as REQUIRED RULE 0 in the [[dftracer-lessons]] skill.

1. **Skills first.** Before installing, building, annotating, running, tuning, or debugging
   anything, run `skill_search` / `graph_query(mode="docs")` / `skill_load` and act on what
   the system already knows. Never reason from model priors or a web search when a
   `workload-*` / `system-*` / `software-*` / `tools-*` skill covers the task.
2. **Every question asked and answer given is a lesson.** A user's answer is durable
   knowledge, not a session detail. Record it AS YOU GO — preferences/"always-never" into the
   relevant skill AND the agent YAML (then `agents_sync`); effort state via
   `memory_write(type=project)`; standing guidance via `memory_write(type=feedback)` with the
   why; deterministic reusable logic into an MCP tool. Review the list with the user in the
   final report rather than asking permission first.
3. **Generic learning propagates everywhere.** App/system/library-scoped lessons go in that
   one skill, but a GENERIC rule must reach ALL skills and agents: add a shared section in
   `common-sections.yaml`, include it in every agent template, run `agents_sync`, reflect it
   in the process skills. A generic rule filed in one corner of one skill has not been
   learned by the system.

**Why:** the user's stated goal is that the system keeps learning from the conversation
itself, not just from run failures — and that a lesson recorded in one place is invisible to
the other 28 agents. Supersedes the earlier "confirm before skill updates" gate
([[feedback-confirm-before-skill-updates]]).

**How to apply:** at the start of any task, `skill_search` first. Immediately after any
AskUserQuestion answer or user correction, write it to the right home before continuing the
work. When the lesson is generic, edit `common-sections.yaml` and re-run `agents_sync`.
