---
name: dftracer-privacy-guard
description: >
  MANDATORY end-of-session privacy validation. Scans everything the session added
  to the git-tracked trees (memory, lessons, skills, agent definitions) for
  identifying content — usernames, absolute user paths, emails, flux job ids,
  session UUIDs, node hostnames — and redacts it deterministically with the
  privacy_scan / privacy_redact MCP tools. Load this before writing to ANY skill,
  lesson, memory, or agent file, and run it as the last step of every session.
---

# Privacy Guard

We learn from experience. We never record who ran it.

Memory, lessons, skills and agent definitions are git-tracked and ship to whoever
installs this package. A session workspace under `workspaces/<session>/` is
gitignored and may hold real resolved paths; **everything else may not**.

## The rule

Before any write to a persisted tree, and again at end of session, the content
must contain none of:

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

**Keep the lesson, drop the provenance.** "Cray HDF5 breaks on `chid_t`" is
knowledge. Who hit it, on which node, in which job, is not.

**Published bibliography is exempt.** A paper's author list on a `**Citation:**`
or `**Authors:**` line is a public reference, not telemetry. Never mangle it —
doing so breaks the citation.

## Steps (end of session)

1. `privacy_scan()` — read-only. Reports every file with identifying content and
   the exact offending substrings. Gitignored files are skipped automatically.
2. Read the findings. Confirm each is a real leak and not a false positive
   (see Lessons below — some things look like emails and are not).
3. `privacy_redact(dry_run=True)` — show exactly which files would change.
4. Present the diff to the user and get confirmation (this writes to tracked
   skills/agents; see [[dftracer-lessons]]' confirmation gate).
5. `privacy_redact()` — rewrite in place.
6. `privacy_scan()` again. It must report `clean`.
7. **Hunt for corner cases: `privacy_suspects()`.** This is the self-learning
   step, and it is not optional. The scan only finds what the rules already
   know. `privacy_suspects` runs heuristic probes (home-like paths, emails, SSH
   URLs, opaque ids, hostnames, IPs, API-key shapes, uid/gid) and reports only
   what `anonymize` *fails* to cover — i.e. precisely the corner cases.
8. Triage every candidate into exactly one of:
   - **Real leak, new class** → `privacy_add_pattern(...)` (step 9), then
     `privacy_redact()` again.
   - **False positive** → tighten the probe in `_SUSPECT_PROBES` and add it to
     the Lessons section below, so the next session is not re-triaged.
   - **Benign and known** → note it; a content hash or a git SHA is neither.
9. `privacy_add_pattern(name, regex, replacement, must_redact, must_not_change,
   structure_safe, note)`. The tool **refuses** a rule unless every
   `must_redact` sample changes and every `must_not_change` sample survives
   byte-identical under the full rule set, rolling back on failure. Always pass
   realistic near-misses in `must_not_change` (a version string, an already-
   anonymous `$USER` path, a citation line). Set `structure_safe=False` if the
   rule reshapes a path — it will then be skipped for `.patch`/`.diff`.
10. Re-run `privacy_scan()` and `privacy_suspects()`. Record what you learned in
    the Lessons section below AND in the agent definition if it changes behavior.

Learned rules live as data in `.agents/workspace/privacy_patterns.yaml`, are
git-tracked, and ship to everyone — a corner case found once is caught forever.
Never hand-edit around the tools.

## Rules

1. **Use the tools, not your judgment.** `privacy_scan` / `privacy_redact` are
   deterministic regex passes over `src/dftracer_agents/privacy.py`. Eyeballing
   prose misses things; the tools do not. If the tool is wrong, fix the tool —
   that is the self-learning contract (CLAUDE.md rule 4).
2. **Scan the persisted trees, PLUS every session's `final_report/` directory.**
   `.agents/skills`, `.agents/agents`, `.agents/workspace` (which includes
   `memory/`) are the standing git-tracked trees. Never scan or redact the REST
   of a live session workspace — it needs its real paths to run. But
   `workspaces/<session>/final_report/` is the one exception: it is a
   self-contained deliverable the user may copy or push elsewhere (per
   Pipeline Policy rule 15), so it must be as clean as the git-tracked trees
   even though it lives inside a gitignored workspace. **Always run
   `privacy_scan()`/`privacy_redact()` on `final_report/` as part of
   `session_final_report`, every session, unconditionally** — not just when a
   push/copy is mentioned. Redact real paths (job ids, hostnames, `$USER`
   segments) to the same placeholders used elsewhere; `config.ini` is the ONE
   file allowed to carry the session's real resolved values (per Pipeline
   Policy rule 15), everything else in `final_report/` must be placeholder-only
   so the package is portable off this machine.
3. **Redaction is not history rewriting.** These tools clean the working tree.
   Content already committed remains in git history; if that matters, say so
   explicitly rather than implying the repo is clean.
4. **New pattern → new rule, persisted.** When you find an identifier class the
   tool misses (a new site's path layout, a new scheduler's job-id format), do
   NOT hand-edit the file. Call `privacy_add_pattern` — it validates the regex
   against your own samples, refuses anything that damages protected content,
   and writes the rule to the git-tracked `privacy_patterns.yaml` so it applies
   to every future session and every user. Corner-case *knowledge* goes in this
   skill's Lessons; deterministic *detection* goes in the tool.
5. **A quiet scan is not a clean repo.** `privacy_scan` only finds what the rules
   know. Always finish with `privacy_suspects`, and treat a non-empty candidate
   list as work, not noise.
6. **Never widen a rule without a near-miss test.** Every `must_not_change`
   sample must include something the greedy version of your regex would eat — a
   version string like `torch==2.10.0+rocm710`, an already-anonymous
   `/p/lustre5/$USER/x`, a `**Citation:**` line. A rule with only trivial
   negatives is how `\d+` gets accepted and silently corrupts the repo.

## Lessons

Real false positives that cost time. Each is now excluded in `privacy.py` —
do not "fix" them back.

- **`/p/lustre5/$USER` flagged as a leak.** The location regex matched its own
  desired output, so a clean file looked dirty and a second redaction pass would
  corrupt it. Fix: a `(?!\$|<|\*|\.\.\.)` guard so a path whose user segment is
  already a placeholder is never re-matched.
- **`git@github.com:LBANN/ScaFFold.git` redacted as an email.** An SSH git remote
  is not a person. Fix: negative lookahead on `git@`.
- **`_ZSt@GLIBCXX_3.4.26` redacted as an email.** ELF symbol version tags look
  like `local@domain`. Fix: reject domains starting with an uppercase letter or
  underscore.
- **`torch==2.10.0+rocm710` became `+rocm<node>`.** A bare `[a-z]{4,}\d{3,4}`
  hostname rule eats wheel/build tags. Fix: reject matches glued to a version by
  a preceding `+ = . - _`, plus a denylist (`rocm`, `cuda`, `python`, `cce`, …).
- **`Write(/home/*)` in `settings.json` flagged.** A glob is not a username.
  Covered by the same placeholder guard.
- **The privacy rule text itself leaked.** The CLAUDE.md example spelled out a
  real username to illustrate what not to store. Write rules about identifiers
  without using a real one.
- **`.env` and `setup-state.json` were git-tracked** and embedded absolute user
  paths. Both are now gitignored, with a committed `.env.example`. When adding a
  generated or machine-local file, gitignore it in the same commit.
- **Collapsing a path inside a `.patch` breaks it.** Rewriting
  `/usr/WS2/<user>/dftracer-agents/...` to `$PROJECT_ROOT/...` changes the
  leading component count, so `patch -pN` strips the wrong number of directories
  and the hunk no longer lands. Fix: `anonymize(..., preserve_structure=True)`
  for `.patch` / `.diff` (see `STRUCTURED_SUFFIXES`) — it replaces only the
  *username segment*, keeping component count, line count, and `@@` headers
  byte-identical. Verify after any patch redaction:

  ```
  wc -l              # unchanged
  grep -c '^@@'      # unchanged
  count('/') per ---/+++ header line   # unchanged
  ```

- **`extra_users` cannot know other people's usernames.** `good-runs/` contained a
  `/usr/workspace/<other-user>/...` path from a collaborator. A rule keyed on the *current*
  user misses it. Fix: depth-preserving `(/usr/workspace\d*/)<seg>` -> `\1$USER`
  patterns that anonymize whatever sits in the username position, whoever it is.
  Always re-scan after redacting; the second pass is what caught this.

### Probe false positives (from `privacy_suspects`)

The first probe run produced 141 candidates and zero real leaks. Each was tuned
away; a probe that cries wolf gets ignored, which is how a real leak ships.

- **`HDF5 1.14.3.3` looked like an IP address.** Fix: only flag an IP in a
  network context (`http://`, `@`, `host=`, `addr=`) and never loopback or
  RFC-1918 ranges.
- **`checkpointFileIntervalTime` looked like a long opaque id.** A camelCase
  config key is not a token. Fix: require the run to be hex and contain digits.
- **`mpif90`, `par2026` looked like hostnames.** Fix: match the real redaction
  rule's shape (`[a-z]{4,}` + 3-4 digits, not glued to a version).
- **`/usr/tce/packages/`, `/usr/global/tools/`, `/usr/share/lmod/` looked like
  user paths.** Fix: a `_SYSTEM_DIRS` denylist checked at *both* the site
  segment and the segment after it — the first attempt only checked one and
  `/usr/tce/` still leaked through.
- **`+@dft_ai.pipeline.evaluate` (a `+` line in a diff) looked like an email.**
  Fix: reject a `+` immediately before the local part.
- **`uid=0` looked like a user id.** That is root. Fix: require `uid=` with 3+
  digits and not `0`.

Correct behavior worth not "fixing": `privacy_suspects` stays silent on
an email address or a `/home/<user>/...` path because `anonymize` already rewrites
them. Probes report only what the rules miss. A probe that fires on covered
content is a bug in the probe.

## Scope

`privacy_scan` / `privacy_redact` cover, relative to the project root:

- `src/dftracer_agents/.agents/skills`
- `src/dftracer_agents/.agents/agents`
- `src/dftracer_agents/.agents/workspace` (includes `memory/`)
- `good-runs` — published reference artifacts: reports, run scripts, configs,
  and the patches under each `final*/` folder. These leak exactly as readily as
  a skill does, and the `.patch` files there need `preserve_structure`.

## Permissions

- **MCP:** `mcp__dftracer__privacy_scan`, `mcp__dftracer__privacy_redact`
- **Write:** only the persisted trees under `src/dftracer_agents/.agents/`, and
  only after user confirmation
- Never `sudo`; never write outside the project root
