---
name: dftracer_isolation
description: Verify that a reproduction package (final_report/, final_provenance/ ...) runs in isolation - from a fresh copy, a clean login shell (env -i bash -l) and only its config.ini - with session_verify_isolation (mode static | launch | status), and rule on it with dftracer_isolation_check. Load before handing a package to anyone, or when a package works only inside the session: hardcoded workspace/home paths, a username or flux job id in scripts, "does not source lib_load_config.sh", "required key ... missing", "references missing ...sh", or an isolated run that exits non-zero.
---

# dftracer_isolation

Converted from dftracer-agents' `mcp_tools/tools/session/isolation.py` (register_isolation_tools).

## Rules
- Order: `session_verify_isolation(run_id, package_dir, mode="static")` and fix EVERY finding -> `mode="launch"` with
  `isolation_root` OUTSIDE the session workspace (on the PFS, e.g. `$LUSTRE_ROOT/$USER`) -> poll `mode="status"` until
  finished -> compare its result with the session's reference result -> `dftracer_isolation_check(run_id, package_dir,
  require_launch=True)`.
- Static rules (what the lint enforces): no session-workspace path, home path, `/<user>/` path or flux job id in any
  script; every script sources the config loader (`lib_load_config.sh` by default); every key the loader `require`s
  is set in `config.ini`; no literal username in config.ini (use `${USER}`); every script `run_all.sh` references exists;
  `bash -n` passes on every `.sh`.
- Launch copies the package into `<isolation_root>/isolation_<ts>/pkg` and starts the entry script detached under
  `env -i HOME USER SSH_AUTH_SOCK TERM=dumb OUTPUT_ROOT=<iso>/out <env_extra> bash -lc ...`; the exit code lands in
  `<iso>/exit_code`, the log in `artifacts/isolation_<ts>.log`, the state in `artifacts/isolation_verify.json`. It
  returns at once.
- Pass only what a clean shell really lacks through `env_extra` (e.g. `REUSE_VENV=1`, `VENV=...`): never paper over a
  package defect with environment.

## Constraints
- Never run the isolated copy inside the session tree; never edit the session's reference results.
- Launch refuses when static findings remain or isolation_root is missing or inside the session workspace.

## Corner cases
- A launch that never writes `exit_code` was killed (allocation ended, node lost): relaunch, do not read it as success.
- `mode="status"` with no recorded launch errors "no isolation launch recorded for this session".
- A flux job id embedded in a comment still fails the lint: use `<flux-jobid>` or a config key.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
For each failed check of `dftracer_isolation_check`:
- `workspace` / `package` -> wrong run_id or package_dir; assemble the package first (session_final_report).
- `static_clean` -> fix each listed finding in the package (paths to config keys, add `source lib_load_config.sh`, add
  required keys to config.ini, add the missing script, fix syntax), then static again.
- `launched` -> `mode="launch"` with an isolation_root outside the session.
- `outside_session` -> relaunch with an isolation_root outside the workspace.
- `finished` -> poll status; relaunch if killed.
- `exit_zero` -> read the log tail; fix the package (missing module load, venv, config key), not the clean shell.
Then predict (session_verify_isolation) and validate again; the same failure twice stops for the user.

## Self-heal
- If the lint is wrong (a false finding on valid shell), fix `_static`'s rule in dftracer-agents' isolation module and
  this capability together (capability_update), with a selftest case for it.

## Self-learn
- What is explored here becomes a capability: a site's clean-shell requirement (modules that must load in bash -l, a
  scheduler wrapper) becomes `system_<machine>` knowledge; a package layout rule extends this skill. See
  capability_builder.

## What works
- `session_verify_isolation(run_id, "final_report", mode="static")` until clean, then `mode="launch",
  isolation_root="$LUSTRE_ROOT/$USER/iso", entry="scripts/run_all.sh", entry_args="2n"`, then `mode="status"` until
  finished with exit 0, then `dftracer_isolation_check(run_id, "final_report", require_launch=True)`.
