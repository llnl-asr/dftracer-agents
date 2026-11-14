---
name: dftracer_session
description: Create and manage a dftracer session workspace - clone (session_create) or import (session_import_local_tree) an application, build the canonical baseline/annotated/opt<n> layout, read/write/edit/remove files inside it, get run paths, list runs, validate and reorganize a drifted layout, report status and collect system info - then rule on it with dftracer_session_check. Load at pipeline stage 1 (session setup), when a tool says "Session <id> not found" / "Workspace not found", when session_create fails on a dangling symlink (FileNotFoundError ... TurbGen.h), when a clone lands on the wrong commit, or when session_validate_structure reports clean=false.
---

# dftracer_session

Pipeline stage 1 of the dftracer pipeline (converted from dftracer-agents' session tools, the
`dftracer-session-setup` agent and the session-layout rules of `dftracer-cheatsheet`/`dftracer-lessons`).
You set up ONE dftracer session and stop: you do not build the app, annotate, trace or optimize.

## Rules
- Tool-first, in this order: `session_create(url, ref)` (or `session_import_local_tree` for a local tree) ->
  `profile_bind(run_id, app, system)` IMMEDIATELY after create succeeds (the first moment a directory exists to hold
  the profile; never bind twice) -> `session_detect` (dftracer_build) -> `session_configure` (dftracer_build) ->
  `session_get_run_paths` -> `session_status` -> `system_detect`. If the tools are not available, stop and ask the
  user to start the dftracer MCP server; if they error, fix the tool or its wiring before any custom Bash.
- Never invent paths for source, build, traces, patches or artifacts. The workspace
  (`workspaces/<app>/<run_id>/`) has ONE canonical layout: `baseline/`, `annotated/`, `opt<n>/` (each with `source/`,
  `scripts/`, `traces/{raw,compact}`, `patches/`), plus `artifacts/`, `scripts/`, `tmp/`, `dataset/`,
  `session_report.md`, `session.json`. Get paths from `session_get_run_paths`, `session_create`,
  `session_init_structure` or `session.json["paths"]` - never construct `ws/build_ann` or `ws/traces` by hand.
- Before annotation and before/after every optimization iteration call `session_validate_structure(run_id)`; if it
  reports `clean: false`, call `session_reorganize_structure(run_id, dry_run=False)` before continuing. It never
  deletes data: drifted paths are quarantined under `artifacts/legacy/<name>/`.
- Session-wide scripts (env.sh, the dftracer install/compose script, any one-off manual script) live in `<WS>/scripts/`;
  per-run compile.sh/run.sh in `<run>/scripts/`. Every log goes to `<WS>/artifacts/` as `<step>_<what>.log`; never
  `<WS>/tmp/` (wrapper scripts and scratch inputs only) and never outside the workspace.
- Use `session_read_file` / `session_write_file` / `session_edit_file` / `session_remove_path` for files in the session:
  they refuse paths that escape the workspace. Always write a COMPLETE file (read the original, build the full content,
  check the line count is >= the original, write once): a partial write truncated ior.c from 1869 to 960 lines.
- `session_collect_system_info` records the node (CPU, memory, NICs, filesystems) into `system_config.json` for the report.
- Validate with `dftracer_session_check(run_id)` before handing the run_id to the build stage.
- Return: run_id, workspace path, build_tool, detected features, and the canonical paths from `session_get_run_paths`.

## Constraints
- Operate only in the session workspace; never `sudo`, never install into system paths.
- Never write identifying content into lessons/skills/agents/memory (usernames, absolute user paths, emails, flux job
  ids, session UUIDs, node hostnames): write `$USER`, `$PROJECT_ROOT`, `$LUSTRE_ROOT`, `$HOME`, `<flux-jobid>`, `<uuid>`.
- Agents propose skill/lesson/tool updates to the main thread; the user confirms before anything is persisted.
- Do not hand-copy a source tree with Bash when `session_create` fails: that is a tool bug to fix.

## Corner cases
- `session_create` fails during the source -> baseline/annotated copy with `FileNotFoundError` on a dangling symlink
  (Flash-X `physics/.../StirMain/TurbGen.h`): the copy must preserve symlinks (`copytree(..., symlinks=True,
  ignore_dangling_symlinks=True)`, which the tool now does). If it recurs, fix the tool, ask the user to restart the
  dftracer MCP server, remove the half-created `workspaces/<rid>/`, and retry.
- A raw commit SHA as `ref`: `git clone --branch` fails; the tool re-clones, deepens (`fetch --unshallow`, then
  `fetch --tags`) and checks out the SHA, and refuses to continue if HEAD is not the requested ref (it used to report
  ok while silently building the default branch).
- Repos that vendor dependencies as submodules (LLNL/RAJAPerf -> RAJA/BLT/camp): the tool runs
  `git submodule update --init --recursive --depth 1`; without it CMake fails with "submodule is not present".
  `.git/objects/pack/*.keep` entries missing in shallow submodule clones are skipped during the copy.
- LLMs pass literal placeholders (`<run_id>`, `{RUN_ID}`) as run ids: `_new_run_id` substitutes a timestamp.
- `session_status` on an unknown id returns "Session <id> not found"; `dftracer_session_check` reports "unknown run".
- A dataset on the parallel file system is linked as `dataset/` (`dataset_path`), never copied into the workspace.
- dftracer MCP server operations (from `dftracer-mcp-server-ops`), for when a session tool is missing or stale:
  - Nothing to do normally: skill/agent/resource text is read at call time (the supervisor re-links new ones); with
    `--ha` a code change starts a new generation, traffic switches when it answers and the old one drains; a dead
    worker is replaced; broken new code leaves the old generation serving with the error in `server_status`.
  - Start the zero-downtime daemon with `dftracer-mcp-server start --ha [--workers 2]` (HTTP, clients at
    `http://host:5000/mcp`; stateless workers behind a gateway, so a rolling restart loses no session). A plain
    `start` / stdio server is one process and cannot swap itself without dropping the connection.
  - From a session (service `admin`): `server_status()` (mode, uptime, `code_changed_since_start`,
    `content_changed_since_start`; under `--ha` generation, workers, swaps, `last_error`, requests, `advice`),
    `server_reload()` (rolling restart now, `--ha` only), `server_refresh_content()` (re-link skills/agents).
    CLI: `dftracer-mcp-server status | reload | stop`.
  - A tool just added is invisible to an already-connected client (FastMCP sends no `tools/list_changed`): the next
    request through the gateway lists it; a single-process server needs an `/mcp` reconnect.
  - `last_error` set + `source_current: false`: the newest edit did not start (see `worker-<generation>.err` in
    `/tmp/$USER/dftracer-agents/`); the old generation still serves. Never `kill` a `--ha` daemon: `reload` or `stop`.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
For each failed check of `dftracer_session_check`:
- `workspace` (unknown run) -> the run_id is wrong or the session was never created: `session_list_runs`/the run_id
  returned by `session_create`; create it if needed.
- `session_json` -> created by hand or corrupted: recreate the session with `session_create`/`session_import_local_tree`.
- `origin_recorded` / `ref_recorded` -> recreate with the exact url and ref so the source is reproducible.
- `baseline_source` / `annotated_source` -> `session_init_structure(run_id)`; if the copy crashed, fix the tool (corner
  cases), remove the half-made workspace and re-run `session_create`.
- `artifacts_dir` / `scripts_dir` / `annotated_traces_*` -> `session_init_structure(run_id)` (idempotent).
Then predict again (the session tool) and validate again. Three attempts to start, more while the failures change;
the same failure twice stops the loop for the user.

## Self-heal
- If a session tool is wrong (copy crash, wrong commit, bad path), fix the tool in this capability (capability_update)
  and ask for a server restart; never reproduce its job by hand while waiting.

## Self-learn
- What is explored here becomes a capability: a new app met at session setup becomes `workload_<app>` or
  `software_<app>` (workload = a scientific/HPC workload traced to study its I/O; software = a tool, service or DL
  training app instrumented as software under test; ask if ambiguous), a new machine `system_<machine>`. See
  capability_builder. Every user answer or correction is a lesson (lesson_add) with the user's words as evidence.

## What works
- 2026-07-07 (Flash-X): `session_create` with symlink-preserving copy (`symlinks=True, ignore_dangling_symlinks=True`).
- RAJAPerf: `session_create` + automatic recursive submodule update (depth 1) before configure.
- Every session: `session_create` -> `profile_bind` -> `session_detect` -> `session_configure` ->
  `session_get_run_paths`, then `dftracer_session_check` passes before the build stage.
