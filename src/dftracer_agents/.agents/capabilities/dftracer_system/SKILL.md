---
name: dftracer_system
description: Detect the system a dftracer session runs on (hostname without trailing digits looked up in systems.yaml - module load order, env with ${VAR}, MPI launcher, sudo, notes) and probe what dftracer can collect there (PAPI hardware-counter budget, derived presets, components, node power). Load as stage 0 of every pipeline, before build or trace steps, when system_detect says "is not in systems.yaml", FileNotFoundError on systems.yaml, the wrong launcher or modules were used, DFTRACER_PAPI_EVENTS has more events than counters (scaled estimates, args.multiplex), or a site env var did not reach a subprocess.
---
Cross-references: [[dftracer_pipeline]], [[system_tuolumne]], [[system_mammoth]], [[system_hetchy]], [[software_flux]]

# dftracer_system

Converted from dftracer-agents' register_system_tools (system_detect, system_list, system_papi_counters,
system_save), the skill dftracer-system-detect (rules, permissions, pitfalls, lessons), the agent
dftracer-system-detect, and the system entries of dftracer-lessons / the annotation lessons log. The
knowledge skill system-detect is a separate capability.

## Rules
- System detection is its own step (stage 0), done once, recorded immediately and reused by every later agent:
  call system_detect() first (MCP tool before any `module list`, `uname` or custom Bash), then
  dftracer_system_check(detect_output=<its markdown>) and report only the facts later agents need: system name,
  launcher and scheduler, module order, env, filesystem/PFS, allocation shape, sudo.
- The system is the hostname with trailing digits stripped (tuolumne1003 -> tuolumne); pass hostname= to plan for
  a compute node from a login node.
- Derive the module env from the APPLICATION, not from defaults: read <app>/scripts/install-<system>.sh,
  <app>/scripts/<app>-<system>.job and pyproject.toml and reuse them verbatim; system_detect gives the site defaults
  only. Report ONE block for both install and run: python version, module set, LD_PRELOAD, LD_LIBRARY_PATH,
  patchelf steps, pip extra + index. An app's python pin (e.g. python/3.11.5) wins over the site default.
- Subprocess-launching tools must merge get_current_system_env() (the system's env with ${VAR} expanded) into their
  env, and a real Cray PE build runs inside a login shell that loads get_current_system_modules() in order;
  hand-picked env vars miss PE_ENV/CRAY_* and break C++ builds.
- PAPI: the budget is the real number of hardware counters (papi_avail lists ~30 presets on a node with ~5
  counters). A derived preset costs 2+ counters. Ask for more than fits and PAPI time-shares and reports scaled
  estimates with exit code 0. Use system_papi_counters(code_type) for the partition (each run keeps PAPI_TOT_CYC as
  the reference so runs are comparable) and verify args.multiplex in the trace. PAPI is never auto-enabled: it runs
  inside the traced process. For gpu/communication/io code CPU presets are the wrong instrument.
- Power is enabled whenever the node can measure it (node-level, collected by dftracer_service, costs the app
  nothing) unless systems.yaml sets tracing.power: off.
- Register an unknown system with system_save (it merges onto the existing entry, keeping the tracing: block it has
  no parameter for) after dftracer_system_check(config=...) passes.

## Constraints
- Read system metadata only; do not change source or build state. system_save is the one write, to systems.yaml.
- Keep the output small and explicit; record module and allocation assumptions for later agents.
- system_detect with probe_features=True shells out to papi_avail under a login shell (seconds); use
  probe_features=False for a pure config lookup.

## Corner cases
- systems.yaml lives at <dftracer-agents repo>/.dftracer_agents/resources/systems.yaml; when that file is absent,
  system_detect, system_list and system_save's read raise FileNotFoundError (PyYAML is installed, so the hand
  parser's missing-file branch is not reached). Moved unchanged; create the file with a `systems:` key or restore
  it, then detect again.
- Not recognised and inside a container (/.dockerenv or /run/.containerenv) -> the `container` entry is used.
- system_save strips trailing digits from the name, and its env values keep ${VAR} literally; only ${VAR} is
  expanded at use, a bare $VAR is not.
- A different python than the app pins produced a swallowed ImportError in dftracer.dftracer (GLIBCXX/RPATH) that
  looked like "tracing produces empty traces".
- Tuolumne: CCE lib dirs and /usr/lib64 must be on LD_LIBRARY_PATH for the dftracer install, and ld.lld's
  --no-allow-shlib-undefined needs -ldl on the link line (LD_LIBRARY_PATH alone does not fix the link).
- Assuming the wrong launcher or filesystem leads to bad downstream routing; reusing stale system assumptions wastes
  the whole session.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
dftracer_system_check names the failed check:
- known_system -> system_save the system from the application's scripts (modules, env, launcher), then
  system_detect and check again.
- system_name / sudo -> pass system_detect's markdown verbatim; the entry needs sudo: true|false.
- launcher_known -> set mpi_launcher to flux run, srun, jsrun, aprun, mpirun or mpiexec.
- papi_budget / papi_runs_fit -> re-run system_papi_counters on a compute node (or pass budget=) and split runs so
  each fits; derived presets cost 2.
- required_keys / key_types / modules / env_var_syntax / tracing / name -> fix the config dict (${VAR}, no duplicate
  modules, name without digits) and validate before system_save.
The same failure twice stops the loop for the user.

## Self-heal
- If system_detect reports something the node contradicts (launcher, counters, power), fix systems.yaml with
  system_save or repair the tool (capability_update); never work around it in shell.

## Self-learn
- What is explored here becomes a capability: see capability_builder. A new machine's facts become (or update)
  system_<machine>; a new launcher or scheduler updates this validator. Update the pitfalls as soon as a new
  site-specific rule appears.

## What works
- (2026-07-06) system_detect(hostname="tuolumne1003") -> tuolumne, flux run, module order and env with ${VAR}
  expanded by get_current_system_env; dftracer_system_check(detect_output=...) passes with scheduler flux.
- (2026-07-08) Detect once at stage 0, record, reuse; the planner calls system_detect while planning.

## Reference: dftracer-system-detect (carried over verbatim)

#### Steps

1. Load the sibling files in this skill directory.
2. Detect system facts with the MCP system-detect tool.
3. Capture module, filesystem, and allocation constraints early.
4. Record any new system-specific pitfall immediately.

#### Included Context

- Load `rules.md`
- Load `permissions.md`
- Load `pitfalls.md`
- Load `lessons.md`

#### Rules

- Treat system detection as its own agent step.
- Use the MCP tool for facts before inferring anything.
- Keep the output small and explicit so later agents can reuse it.
- Update lessons when the system behaves differently than expected.

#### Permissions

- Read system metadata only.
- Do not change source or build state.
- Record the current allocation and module assumptions for later agents.

#### Pitfalls

- Assuming the wrong launcher or filesystem leads to bad downstream routing.
- Reusing stale system assumptions wastes the whole session.
- System facts should be recorded before build or trace steps start.

#### Lessons Learned

- Detect once, record immediately, and reuse the result.
- Update the system pitfalls as soon as a new site-specific rule appears.
- Keep the detection agent narrow so it remains cheap and reliable.

