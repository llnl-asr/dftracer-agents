---
name: feedback_learn_trigger_routes_to_skills
description: "'learn' is a trigger word: route the lesson into project skills/agents/MCP tools directly; use memory_write (not raw Edit) for any memory pointer"
metadata: 
  node_type: memory
  type: feedback
---

**Canonical home:** see the `dftracer-lessons` skill ("Growing the skills every
session" routing section).

**CORRECTED 2026-08-02:** this file originally described "personal Claude
memory" as a separate, harness-specific system from the project's own
skills/agents/tools. That was wrong. Per CLAUDE.md's own "Memory" section:
this memory store (`src/dftracer_agents/.agents/workspace/memory/`) is the
SAME store for claude/opencode/copilot/codex — Claude Code just additionally
exposes it natively via a symlink at `~/.claude/projects/<slug>/memory/`.
There is only one memory system, not two.

**The real distinction that matters is the ACCESS PATH, not the store:**
- `memory_write` (the MCP tool) anonymizes deterministically and rejects
  `type=user` — this is the safe, portable way to write here.
- Editing a file directly (raw `Edit`/`Write`) at the symlinked
  `~/.claude/projects/.../memory/` path bypasses that anonymization. Claude
  Code's own native memory feature treats that path as its private
  session-scoped store and auto-stamps ambient bookkeeping metadata into the
  frontmatter on write — a per-session origin identifier plus a modification
  timestamp — which puts a real session identifier into a git-tracked file,
  directly violating CLAUDE.md Pipeline Policy rule 9. **Confirmed
  real incident (2026-08-02):** a retrofit pass touched 65 memory files via
  raw `Edit`/`Write` and 2 of them picked up that identifier field — caught
  by a manual grep after the fact. `privacy_scan` DOES flag the field name
  itself as a substring match (confirmed: it flagged this very file for
  merely describing the field name in prose, a false positive worth noting
  to `dftracer-privacy-guard` — it currently can't distinguish "a real
  identifier value" from "documentation mentioning the field name"), so
  `privacy_scan` is a reasonable backstop for the real case, just don't
  write the literal field name verbatim in memory prose without a values-only
  placeholder.

**When the user says "learn" (as an explicit trigger, e.g. "learn this",
"remember this as a skill"):**
1. Identify the right skill (`workload-<app>` / `system-<system>` /
   `software-<lib>` / a generic `dftracer-*` skill) or agent YAML, write/update
   it there directly, run `agents_sync` if an agent YAML changed.
2. If a memory pointer is also useful for cross-session continuity, write it
   with `memory_write` (not raw `Edit`) so it gets anonymized correctly — the
   pointer should read like "see `workload-af3` skill for X" rather than
   restating the technical content, but that's about avoiding duplication of
   CONTENT, not about avoiding the memory store itself.
3. Still applies: [[feedback_confirm_before_skill_updates]] — propose before
   persisting to skills, even under a "learn" trigger, unless the user's
   "learn" message already IS the confirmation.
4. If a lesson has NO project-side home yet (an orphaned memory-only lesson),
   create the missing skill/agent content first, then link back from memory.

**After ANY batch edit to files under this memory directory (via raw
Edit/Write, not `memory_write`):** grep the touched files' frontmatter for an
origin-session identifier field or a modification-timestamp field before
considering the pass done, and strip either if present — `privacy_scan`
catches the real case too, but check yourself rather than relying on it
alone for a batch pass.
