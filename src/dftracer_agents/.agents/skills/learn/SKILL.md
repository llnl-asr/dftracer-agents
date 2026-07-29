---
name: learn
description: The "learn" command. When the user says "learn <topic>" (or "learn about X", "go learn this"), treat it as an instruction to acquire knowledge and PERSIST it into the right home — a skill, an agent definition, and/or an MCP tool — rather than just answering in chat. Covers how to gather, how to verify, how to route, how to anonymize, and what to ask the user for. Load this skill whenever the user's message contains the word "learn" as an imperative.
---

# learn

`learn` is a verb the user uses deliberately. It does NOT mean "explain this to me
in chat". It means: **go acquire this knowledge and durably persist it** so that
every future session starts already knowing it.

A `learn` request is only complete when something on disk changed.

## The five steps

### 1. Scope it — and say what you scoped

Restate the topic as a concrete list of things you will learn, and name the
homes you expect to write to. If the request is broad ("learn about our sites"),
decompose it into what is common (goes in one shared skill) and what is
per-instance (goes in one skill each). Do this BEFORE gathering — it stops you
producing one giant unfocused file.

### 2. Gather — prefer the live system over prose

Ranked by trustworthiness:

1. **The live system.** Run the command. `module avail`, `bankinfo`, `flux queue
   list`, `sinfo`, `/etc/*-release`, `lscpu`, `rocminfo`. A fact you verified by
   executing something is worth more than a fact you read.
2. **User-supplied resources.** URLs, pasted docs, file paths they hand you.
3. **The public web.** `WebFetch` / `WebSearch`.
4. **Existing repo content.** Other skills, git history, source.

Record HOW each fact was established. A skill that says "verified 2026-07-28 via
`flux queue list`" ages far better than one that just asserts.

### 3. ASK when you are blocked or guessing

This is the step that gets skipped, and skipping it is what produces confidently
wrong skills. The user has explicitly invited questions — use that.

Ask when:
- **A source is unreachable.** Site-internal docs frequently do not resolve from
  compute/login hosts. Say exactly which URL failed and how (timeout vs 404 vs
  auth), and ask them to paste the content or point you at a reachable mirror.
- **You cannot reach the thing you are documenting.** Do not write a skill about
  a cluster you have never touched from facts you inferred. Ask whether you may
  log into it, or ask them to run a short command block and paste the output.
- **The scope is ambiguous** in a way that changes what you write.
- **You would otherwise guess a version, path, or flag.**

Never pad a skill with plausible-sounding filler to make it look complete. An
explicit `TODO — not yet verified, need <X>` is far more useful than a confident
sentence that is wrong. Mark unverified content inline.

### 4. Route it to the correct home

Knowledge goes where it will actually be found. More than one home is normal.

| Kind of knowledge | Home |
|---|---|
| Site-wide facts shared by every cluster at a site (banks, scheduler, zones, filesystems, module system) | `site-<site>` skill |
| One specific cluster/system (hardware, queues, quirks) | `system-<system>` skill |
| A library/tool/build system (HDF5, MPI, ROCm, CMake) | `software-<lib>` skill |
| A traced scientific workload | `workload-<app>` skill |
| A behavioral rule for one agent | that agent's YAML under `.agents/agents/*.yaml` (then run `agents_sync`) |
| Deterministic, reusable logic that should run identically every time | an **MCP tool** under `src/dftracer_agents/mcp_tools/` — not prose |
| A durable user preference or correction | memory (`memory_write`) |

Cross-link related skills with `[[skill-name]]`. A link to a skill that does not
exist yet is fine — it marks the next thing worth learning.

**Prefer editing an existing skill over creating a near-duplicate.** Check first.

### 5. Anonymize, then verify deterministically

Everything under `src/` is git-tracked and ships to other people. Before
finishing, strip:

- usernames, real names, emails, account handles
- absolute user paths → `$PROJECT_ROOT`, `$HOME`, `$USER`, `$LUSTRE_ROOT`
- bank/account/allocation IDs → `<bank>`, `<alloc-id>`
- flux job ids → `<flux-jobid>`; session UUIDs → `<session>`
- hostnames with node numbers → `<node>`

Keep the lesson, drop the provenance. Then run `privacy_scan()` and fix whatever
it flags — verify with the tool, not by re-reading.

## Finish by reporting what changed

End with the concrete list: files created/edited, whether `agents_sync` was run,
whether `privacy_scan` came back clean, and **what you could not learn and why**
(with the specific question the user could answer to unblock it).

## Related

[[dftracer-lessons]] for the lessons-cache mechanics, [[dftracer-privacy-guard]]
for the scan/redact tooling, [[dftracer-context-economy]] for locating code
without reading whole trees.
