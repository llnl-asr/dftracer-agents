---
name: feedback_pipeline_selflearning
description: "dftracer pipeline design — session-first, planner writes sectioned plan into session, all agents self-learn into skills"
metadata: 
  node_type: memory
  type: feedback
  
---

Design rules for the dftracer agent pipeline (from user, 2026-07-07):

1. **Session first, then plan.** Main thread creates OR resumes a session and asks the user new-vs-resume BEFORE running the planner. The `dftracer-pipeline-planner` requires a `run_id` and never creates a session itself.
2. **Planner writes a sectioned plan into the session.** It writes `pipeline_plan.md` (via `session_write_file`, subfolder="." fallback "scripts") with an `## Overview` plus one `## STEP N: <exact-agent-name>` section per step agent. Each section is self-contained so step agents load their own section and DO NOT replan. Planner returns only a short summary + DISPATCH ORDER.
3. **Every step agent loads its plan section first** via `session_read_file` before acting.
4. **Self-learning is mandatory for ALL agents.** On finishing, each agent records non-obvious lessons (symptom → root cause → exact fix) into the correct skill by scope: `workload-<app>` (build/run caveats), `system-<system>` (site/env quirks), `software-<lib>` (HDF5/MPI/compiler/language). Build agents → workload build caveats; tracer/run agent → workload run lessons; system-detect → system skill. Create the skill if missing.

**Why:** step agents start cold; a detailed persisted plan avoids re-derivation, and skill feedback makes the system improve run over run.

**How to apply:** edits live in `src/dftracer_agents/.agents/agents/*.md`; re-materialize with `ensure_agents_setup(force=True)`. See [[project_claude_agent_models]] for the model-resolution fix that makes these agents spawnable.

5. **User has repeated this rule explicitly multiple times (2026-07-10, h5bench session):** self-learning is not optional or occasional — EVERY pipeline interaction that discovers something new (build quirks, config-schema differences, ROMIO/Lustre findings, per-workload sample-config choices, etc.) must persist it back into the right skill, and if it changes agent behavior, into the agent's own YAML template too, plus a new/fixed MCP tool for generic deterministic logic (Pipeline Policy rule 10). Don't treat this as a one-off ask for the current session — apply it by default on every future session without being asked again.
