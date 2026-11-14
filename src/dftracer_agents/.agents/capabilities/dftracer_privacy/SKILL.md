---
name: dftracer_privacy
description: MANDATORY end-of-session privacy validation for dftracer-agents. Scan everything the session added to the git-tracked trees (memory, lessons, skills, agent definitions, good-runs, scripts, package source) and every final_report/ for identifying content (usernames, absolute user paths, emails, flux job ids, session UUIDs, node hostnames), redact it deterministically with privacy_scan / privacy_redact, hunt uncovered corner cases with privacy_suspects, and persist validated rules with privacy_add_pattern. Load before writing to ANY skill, lesson, memory, agent file or package, and run it as the last step of every session.
---
Cross-references: [[dftracer_report]], [[dftracer_lessons]], [[dftracer_skills]], [[lessons]], [[knowledge]]

# Privacy guard

We learn from experience. We never record who ran it.

Memory, lessons, skills and agent definitions are git-tracked and ship to whoever installs this package. A session
workspace under `workspaces/<session>/` is gitignored and may hold real resolved paths; **everything else may not**,
including that session's own `final_report/`.

## Rules

### What never persists

| Never persist | Write instead |
| --- | --- |
| any username, real name, or handle | `$USER` |
| `/usr/WS2/<user>/dftracer-agents` | `$PROJECT_ROOT` |
| `/p/lustre5/<user>`, `/p/vast1/<user>` | `$LUSTRE_ROOT`, `$VAST_ROOT` |
| `/g/g92/<user>`, `/home/<user>` | `$HOME` |
| email addresses | `<redacted-email>` |
| flux job ids (`f3<11 alnum chars>`) | `<flux-jobid>` |
| session UUIDs, `originSessionId` | `<uuid>` / drop the line |
| node hostnames (`<system>` + 3-4 digits) | `<system><node>` |
| a `type: user` profile memory | do not write one at all |

**Keep the lesson, drop the provenance.** "Cray HDF5 breaks on `chid_t`" is knowledge. Who hit it, on which node, in
which job, is not. **Published bibliography is exempt**: a paper's author list on a `**Citation:**` or `**Authors:**`
line is a public reference, not telemetry; never mangle it.

### Steps (end of session)
1. `privacy_scan()`: read-only; every file with identifying content and the exact offending substrings. Gitignored
   files are skipped automatically (except anything under `final_report/`).
2. Read the findings; confirm each is a real leak and not a known false positive (Corner cases).
3. `privacy_redact(dry_run=True)`: exactly which files would change.
4. Present the diff to the user and get confirmation (these are tracked source files; confirmation gate).
5. `privacy_redact()`: rewrite in place.
6. `privacy_scan()` again: it must report `clean`.
7. **Hunt for corner cases: `privacy_suspects()`** (not optional, even after a clean scan). Heuristic probes (home-like
   paths, emails, SSH URLs, opaque ids, hostnames, IPs, API-key shapes, uid/gid) report only what `anonymize` fails to cover.
8. Triage every candidate into exactly one of: **real leak, new class** -> `privacy_add_pattern(...)`, then
   `privacy_redact()` again; **false positive** -> tighten the probe in `_SUSPECT_PROBES` (privacy.py) and add it to the
   lessons; **benign and known** -> say so (a content hash or a git SHA identifies nothing).
9. `privacy_add_pattern(name, regex, replacement, must_redact, must_not_change, structure_safe, note)`: refused unless
   every `must_redact` sample changes and every `must_not_change` sample survives byte-identical under the full rule
   set (rolled back on failure). Pass realistic near-misses in `must_not_change` (a version string, an
   already-anonymous `$USER` path, a citation line). `structure_safe=False` if the rule reshapes a path (then skipped for
   `.patch`/`.diff`).
10. Re-run `privacy_scan()` and `privacy_suspects()`; `dftracer_privacy_check(<absolute paths>)` on anything outside
   the package trees (final_report/, a capability dir). Record what you learned in this skill's lessons AND in the agent
   definition if it changes behavior.

Learned rules live as data in `.agents/workspace/privacy_patterns.yaml`, git-tracked, shipping to everyone: a corner
case found once is caught forever. Never hand-edit around the tools.

### Rules
1. **Use the tools, not your judgment.** `privacy_scan` / `privacy_redact` are deterministic regex passes over
   `src/dftracer_agents/privacy.py`. If the tool is wrong, fix the tool (the self-learning contract).
2. **Scan the persisted trees, PLUS every session's `final_report/`.** `.agents/skills`, `.agents/agents`,
   `.agents/workspace` (with `memory/`) are the standing git-tracked trees. Never scan or redact the REST of a live
   session workspace: it needs its real paths to run. `workspaces/<session>/final_report/` is the one exception: a
   self-contained deliverable the user may copy or push elsewhere (Pipeline Policy rule 15), so always run
   `privacy_scan()`/`privacy_redact()` on it as part of the report stage, every session, unconditionally. `config.ini` is
   the ONE file allowed to carry the session's real resolved values; everything else is placeholder-only.
3. **Redaction is not history rewriting.** These tools clean the working tree; content already committed remains in git
   history. Say so explicitly rather than implying the repo is clean.
4. **New pattern -> new rule, persisted** with `privacy_add_pattern`, never a hand edit. Corner-case *knowledge* goes in
   the lessons; deterministic *detection* goes in the tool.
5. **A quiet scan is not a clean repo.** Always finish with `privacy_suspects`; a non-empty candidate list is work, not noise.
6. **Never widen a rule without a near-miss test.** Every `must_not_change` must include something the greedy version of
   the regex would eat (`torch==2.10.0+rocm710`, `/p/lustre5/$USER/x`, a `**Citation:**` line). A rule with only trivial
   negatives is how `\d+` gets accepted and silently corrupts the repo.
7. **Never report `clean` you did not observe**: paste the tool's own result. Never call a candidate benign because
   triaging it is tedious.
8. **Never write a `type: user` profile memory**, and delete one if you find it.

## Constraints
- Write only the persisted trees under `src/dftracer_agents/.agents/`, and only after user confirmation. Never `sudo`;
  never write outside the project root.
- Scope of `privacy_scan` / `privacy_redact` (project-root relative): `src/dftracer_agents/.agents/skills`,
  `src/dftracer_agents/.agents/agents`, `src/dftracer_agents/.agents/workspace` (includes `memory/`), `good-runs`
  (published reference artifacts: reports, run scripts, configs, and the patches under each `final*/` folder; the
  `.patch` files need `preserve_structure`), `scripts`, and `src/dftracer_agents`; anything under a `final_report/` is
  always scanned. Files that define the patterns (privacy.py, privacy_tools.py, system_service.py, the privacy skill,
  privacy_patterns.yaml) are excluded.
- `privacy_scan`'s `paths` are package-relative; `dftracer_privacy_check` takes absolute paths.
- If `privacy.py` changed, ask the user to restart the MCP server so the tool reloads.

## Corner cases
Real false positives that cost time; each is excluded in `privacy.py`, do not "fix" them back:
- `/p/lustre5/$USER` flagged as a leak: the location regex matched its own output, so a second pass would corrupt it.
  Fix: a `(?!\$|<|\*|\.\.\.)` guard so a path whose user segment is already a placeholder is never re-matched.
- `git@github.com:LBANN/ScaFFold.git` redacted as an email: an SSH git remote is not a person. Fix: negative lookahead on `git@`.
- `_ZSt@GLIBCXX_3.4.26` redacted as an email: ELF symbol version tags look like `local@domain`. Fix: reject domains
  starting with an uppercase letter or underscore.
- `torch==2.10.0+rocm710` became `+rocm<node>`: a bare `[a-z]{4,}\d{3,4}` hostname rule eats wheel/build tags. Fix:
  reject matches glued to a version by a preceding `+ = . - _`, plus a denylist (`rocm`, `cuda`, `python`, `cce`, ...).
- `Write(/home/*)` in `settings.json` flagged: a glob is not a username; covered by the placeholder guard.
- The privacy rule text itself leaked: an example spelled out a real username. Write rules about identifiers without
  using a real one.
- `.env` and `setup-state.json` were git-tracked with absolute user paths: both gitignored now, with a committed
  `.env.example`. A generated or machine-local file is gitignored in the same commit that adds it.
- Collapsing a path inside a `.patch` breaks it: `$PROJECT_ROOT/...` changes the leading component count, so
  `patch -pN` strips the wrong directories. Fix: `anonymize(..., preserve_structure=True)` for `.patch`/`.diff`
  (`STRUCTURED_SUFFIXES`): replaces only the username segment, keeping component count, line count and `@@` headers.
  Verify after any patch redaction: `wc -l` unchanged, `grep -c '^@@'` unchanged, `/` count per `---`/`+++` header unchanged.
- `extra_users` cannot know other people's usernames: `good-runs/` had a `/usr/workspace/<other-user>/...` path from a
  collaborator. Fix: depth-preserving `(/usr/workspace\d*/)<seg>` -> `\1$USER` patterns that anonymize whatever sits
  in the username position. Always re-scan after redacting; the second pass caught this.

Probe false positives (from `privacy_suspects`; the first run gave 141 candidates and zero real leaks; a probe that
cries wolf gets ignored, which is how a real leak ships):
- `HDF5 1.14.3.3` looked like an IP: only flag an IP in a network context (`http://`, `@`, `host=`, `addr=`), never
  loopback or RFC-1918 ranges.
- `checkpointFileIntervalTime` looked like an opaque id: require the run to be hex and contain digits.
- `mpif90`, `par2026` looked like hostnames: match the real redaction rule's shape (`[a-z]{4,}` + 3-4 digits, not glued
  to a version).
- `/usr/tce/packages/`, `/usr/global/tools/`, `/usr/share/lmod/` looked like user paths: a `_SYSTEM_DIRS` denylist
  checked at BOTH the site segment and the one after it (checking one let `/usr/tce/` through).
- `+@dft_ai.pipeline.evaluate` (a `+` line in a diff) looked like an email: reject a `+` immediately before the local part.
- `uid=0` looked like a user id: that is root; require `uid=` with 3+ digits, not `0`.
Correct behavior worth not "fixing": `privacy_suspects` stays silent on an email or a `/home/<user>/...` path because
`anonymize` already rewrites them; probes report only what the rules miss.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
- `dftracer_privacy_check` fails on a file -> `privacy_redact` (package trees) or `anonymize` the file through the tool
  path you used to write it, then check again; a `.patch`/`.diff` keeps its structure (`preserve_structure`).
- The finding is a false positive -> fix the rule or probe in `privacy.py` with a unit case, never tolerate a dirty scan.
- A real leak the rules do not know -> `privacy_add_pattern` with realistic `must_not_change` near-misses; when it is
  refused ("rule does not redact its own samples" / "rule rewrites content that must not change; rolled back"), narrow
  the regex and retry.
- `inconclusive` -> the paths named no file: pass absolute paths.
The same failure twice stops the loop: ask the user.

## Self-heal
- A tool is missing or wrong: fix `src/dftracer_agents/privacy.py` (pattern or exclusion, with a unit case) or
  `capability_update` this capability; restart the MCP server so it reloads.

## Self-learn
- What is explored here becomes a capability: a new site's path layout or a new scheduler's job-id format becomes a
  validated rule (`privacy_add_pattern`) plus a lesson here, and its site knowledge belongs in `system_<machine>`; see
  capability_builder.
- Persist BOTH the deterministic rule and the human-readable lesson; learned rules ship to every user.

## What works
- `privacy_scan` -> `privacy_redact(dry_run=True)` -> user confirmation -> `privacy_redact` -> `privacy_scan` clean ->
  `privacy_suspects` triage -> `privacy_add_pattern` for real classes; repeated every session as the last step.
- Patches: `preserve_structure=True` keeps `patch -pN` working (verified by line count, `@@` count, `/` per header).
