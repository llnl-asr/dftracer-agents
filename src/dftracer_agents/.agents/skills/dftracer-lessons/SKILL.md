---
name: dftracer-lessons
description: How to use and update the lessons-learned cache at .goose/lessons-learned.md — proactive lookup before any build/install/annotate step
---

## REQUIRED RULE 0: Skills first, and capture every Q&A as a lesson (MANDATORY)

Before installing, building, annotating, running, tuning, or debugging anything: run
`skill_search` / `graph_query(mode="docs")` / `skill_load` and act on what the system
already knows. Never reason from model priors or a web search when a
`workload-*` / `system-*` / `software-*` / `tools-*` skill already covers the task.

Every question the user answers, and every correction the user gives, is durable
knowledge — not a session detail. Record it AS YOU GO, not just at the end:
preferences/"always-never" rules into the relevant skill AND the agent YAML (then
`agents_sync`); effort/session state via `memory_write(type=project)`; standing
guidance via `memory_write(type=feedback)` with the why; deterministic reusable
logic into an MCP tool.

A GENERIC rule (one that should apply to every agent, not just one skill's scope)
must reach ALL skills and agents: add a shared section in
`agents/common-sections.yaml`, `include:` it in every agent template that needs it,
run `agents_sync`, and reflect it in the process skills. A generic rule filed in one
corner of one skill has not been learned by the system — this is the same shared
`skills-first-and-capture-every-qa` section every agent template includes.

This governs WHEN and WHAT to capture; the "Confirmation gate" below still governs
whether a captured lesson gets PERSISTED to a shared skill/tool/agent without the
user's sign-off first.

## Lessons-Learned Cache

A structured log of past failures and their fixes lives at `.goose/lessons-learned.md`.

**Before attempting any build, install, configure, or annotation step:**
1. Read `.goose/lessons-learned.md`.
2. Search for entries whose `tags` or `error` match the current task (package name, build tool, error text).
3. Apply the recorded fix proactively — do not repeat a known failure.

**After resolving any non-trivial error (not a simple typo):**
Append a new entry to `.goose/lessons-learned.md` using this format:

```markdown
---
date: YYYY-MM-DD
context: <one-line description of what was being attempted>
error: |
  <exact error message or key excerpt>
root_cause: <why it happened>
fix: |
  <exact steps or code change that resolved it>
tags: [<package>, <build-tool>, <error-keyword>, ...]
---
```

Keep entries cumulative — never delete old ones. They are the institutional memory for this project.

## Naming disambiguation: `workload-<app>` vs `software-<app>` (MANDATORY)

Before creating a new per-app skill, decide which family it belongs to —
this governs ~20 existing skill names and is not otherwise documented in any
skill or agent:

- **`workload-<app>`** — a scientific/HPC workload dftracer TRACES to study
  I/O behavior (the thing under study). Examples: `workload-ior`,
  `workload-h5bench`, `workload-flashx`, `workload-vpic-kokkos`,
  `workload-scaffold`, `workload-1000genome`.
- **`software-<app>`** — a tool, service, or DL application that is itself
  being ANNOTATED/INSTRUMENTED by dftracer as software under test, not a
  scientific workload in its own right. Examples: `software-flux-fiction`
  (a Flux scheduling emulator), `software-molformer`, `software-pecan`,
  `software-ray` (annotated DL training apps use `software-*`, not
  `workload-*`, even though they are the thing being traced — the
  distinction is "scientific/HPC I/O workload" vs "software application").

**Why:** an auto-created `workload-flux-fiction` skill was corrected by the
user, who clarified flux-fiction is software annotated by dftracer, not a
workload. The same distinction applies to every annotated app in this
project, not just that one instance.

**How to apply:** before creating a new per-app skill during a build/annotate
session, judge whether the target is a traced scientific workload
(`workload-*`) or a tool/service/DL-training-app being instrumented
(`software-*`), and ask the user if genuinely ambiguous rather than
defaulting to `workload-*`.

## Growing the skills every session (generic vs specific routing)

Every agent is expected to make the skills smarter each run — capture not just
failures but the working recipe (exact commands, flags, paths, versions,
caveats) so the next session reuses it instead of rediscovering it.

Route each learning to the right home:

- **Generic, cross-workload knowledge** → the relevant GENERIC skill
  (e.g. `dftracer-annotate-*`, `software-hdf5`, `software-mpi`, `software-posix`).
  Keep these skills generic: they hold the general procedure that applies
  everywhere.
- **Workload-specific** caveats → `workload-<app>` (e.g. `workload-flashx`).
- **System/site-specific** quirks → `system-<system>` (e.g. `system-tuolumne`).
- **Library-specific** details → `software-<lib>`.
- Create the specific skill if it does not exist.

Prefer: generic skill = the general how-to; specific skill = only the
workload/system/software deltas layered on top.

## Redaction gate (MANDATORY)

Lessons, skills, agent definitions and memory are git-tracked and ship to other
people. **Redact before you persist.** We learn from experience; we never record
who ran it.

Never write into any of them: usernames or real names, email addresses, absolute
user paths (`/usr/WS2/<user>/...`, `/p/lustre5/<user>/...`, `/g/g92/<user>/...`,
`/home/<user>/...`), flux job ids, session UUIDs, or node hostnames. Write
`$USER`, `$PROJECT_ROOT`, `$LUSTRE_ROOT`, `$HOME`, `<flux-jobid>`, `<uuid>`,
`<system><node>` instead. Keep the lesson; drop the provenance.

Verify with the deterministic tools rather than by reading:

```
privacy_scan()                 # report identifying content, read-only
privacy_redact(dry_run=True)   # show what would change
privacy_redact()               # rewrite in place
```

Citation lines (`**Citation:**`, `**Authors:**`) are public bibliography and are
exempt. The `dftracer-privacy-guard` agent runs this as the last step of every
session, but that is a backstop — do not rely on it to clean up after you. Load
[[dftracer-privacy-guard]] for the full table and the known false positives.

## Confirmation gate (MANDATORY)

Agents must NOT self-write skills, lesson files, agent definitions, or MCP
tools. Instead, PROPOSE each update back to the main thread (target skill/tool
+ what you did / symptom → root cause → exact content). The main thread confirms
the observation with the user, and only then is it persisted. This keeps
incorrect diagnoses out of the shared institutional memory while still capturing
learning aggressively.
