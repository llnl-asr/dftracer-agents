---
name: feedback-privacy-anonymous
description: Everything persisted (memory, lessons, skills, agents) is git-tracked and must be anonymous; verify with privacy_scan
metadata:
  type: feedback
---

**Canonical home:** see the `dftracer-privacy-guard` skill and the `redact-before-you-persist`
shared section in `agents/common-sections.yaml`, plus the `dftracer-lessons` skill's
Redaction gate.

Memory, lessons, skills and agent definitions are git-tracked and ship to whoever
installs this package. **We learn from experience but never record who ran it.**

**Why:** the previous memory store lived in `$HOME/.claude/projects/<slug>/memory/`
— outside the repo, untracked, invisible to anyone else installing the package.
Knowledge died with the machine, and what was written contained usernames, absolute
user paths, emails, flux job ids and node hostnames.

**How to apply:**
- The real memory files now live in `src/dftracer_agents/.agents/workspace/memory/`.
  `ensure_workspace_setup` (bootstrap.py, run on MCP server start) symlinks the
  harness path at them, migrating any files it finds. Edit the `src/` target.
- Never write a `type: user` profile memory.
- Never persist usernames, real names, emails, absolute user paths, flux job ids,
  session UUIDs, or node hostnames. Use `$USER`, `$PROJECT_ROOT`, `$LUSTRE_ROOT`,
  `$HOME`, `<flux-jobid>`, `<uuid>`, `<system><node>`.
- Keep the lesson, drop the provenance. Citation author lists are public
  bibliography and are exempt.
- Verify with `privacy_scan()` / `privacy_redact()` — deterministic regex tools in
  `src/dftracer_agents/privacy.py`, not by reading. The `dftracer-privacy-guard`
  agent runs this as the final step of every session.
- A live session workspace under `workspaces/<session>/` is gitignored and keeps
  its real paths; the rule applies to the persisted trees only.
- `.env` and `.agents/workspace/setup-state.json` embed absolute paths and are now
  gitignored (`.env.example` is committed instead). Gitignore machine-local files
  in the same commit that creates them.

Caveat worth repeating out loud: redaction cleans the working tree. Content already
committed remains in git history.

Related: [[feedback-pipeline-selflearning]], [[feedback-confirm-before-skill-updates]],
[[feedback-profiling-at-session-create]]
