---
name: dftracer_pipeline_run
description: Create, recall and list dftracer pipeline runs (run_id = <app_name>/<YYYYMMDD_HHMMSS>, workspace <workspaces root>/<run_id>/ with session.json, active run in <app_name>/.current_run). Load when a stage needs a run_id, "No runs found for app", "No active run pointer for app", a run_id that does not match its app, the wrong workspace root (DFTRACER_WORKSPACES), or to see which run is current.
---
Cross-references: [[dftracer_pipeline]], [[pipeline_control]]

# dftracer_pipeline_run

Converted from dftracer-agents' register_run_tools (pipeline_create_run, pipeline_get_run_id, pipeline_list_runs)
and the run-ID rules of the dftracer-pipeline / dftracer-pipeline-planner skill and agent.

## Rules
- A pipeline starts by creating its run: pipeline_create_run(app, description) -> run_id, app_name, workspace,
  created_at. The run id is `<app_name>/<YYYYMMDD_HHMMSS>`; app_name is derived from app (basename, lower case,
  extensions and URL/path structure stripped, non-alphanumeric runs -> `_`), so `ior`, `/path/to/ior` and
  `https://github.com/org/ior.git` all give `ior`.
- Recall the active run with pipeline_get_run_id(app) instead of tracking it by hand; pipeline_list_runs(app) lists
  every run with created_at, step and is_current.
- Validate every result with dftracer_pipeline_run_check(result, app) before handing a run_id to a stage agent, and
  with check_files=True when the run will be resumed later.
- Carry the run_id and the canonical paths forward between steps; get paths from session_get_run_paths or
  session.json["paths"], never hand-build them.

## Constraints
- The workspaces root is DFTRACER_WORKSPACES (default `workspaces`, resolved against the current directory at call
  time): every tool of a session must see the same value, or runs "disappear".
- pipeline_create_run moves the app's .current_run pointer to the new run; it never deletes earlier runs.
- Do not write into another run's workspace; each optimization iteration lands in its own opt<n>/ of the run.

## Corner cases
- pipeline_get_run_id with no pointer but existing run directories returns an error listing available_runs: pick
  one explicitly; with no runs at all it says to call pipeline_create_run.
- A run directory without session.json is not listed (it was not created by the run tools).
- LLM placeholders such as `<run_id>` or `{RUN_ID}` passed as a run id are replaced by a fresh timestamp
  (_new_run_id): a run created that way is a NEW run, not the one meant.
- session_run_pipeline creates its own run (from the URL) when run_id is not given.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
dftracer_pipeline_run_check names the failed check:
- status (No runs found) -> pipeline_create_run(app), then validate again; (No active run pointer) -> choose one of
  available_runs or create a new run.
- app_name / run_id_app -> pass the same app value to every tool; use the run_id returned for that app.
- run_id_shape -> use the run_id exactly as returned (<app_name>/<run>).
- workspace_path / workspace_exists / session_json -> the workspaces root changed (DFTRACER_WORKSPACES or the
  current directory); call again with the same root, or create a new run.
- current_pointer / one_current -> another run became current: use pipeline_get_run_id or pass run_id explicitly.
The same failure twice stops the loop for the user.

## Self-heal
- If a run tool is wrong (e.g. derives a different app_name for the same app), repair it with capability_update;
  do not create directories by hand.

## Self-learn
- What is explored here becomes a capability: see capability_builder. A new run layout or pointer rule updates this
  capability's tools and validator; app naming quirks are lessons here.

## What works
- (2026-07-06) pipeline_create_run(app="ior") -> {"run_id": "ior/<YYYYMMDD_HHMMSS>", "workspace":
  "<root>/ior/<YYYYMMDD_HHMMSS>"}; pipeline_get_run_id(app="ior") returns the same run; dftracer_pipeline_run_check
  passes on both.
