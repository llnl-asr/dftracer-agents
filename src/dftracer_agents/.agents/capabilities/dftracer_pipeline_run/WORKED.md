# What worked: dftracer_pipeline_run

- [tool:pipeline_create_run] 2026-07-06: pipeline_create_run(app="<app or git URL>", description="<why>") -> run_id <app_name>/<YYYYMMDD_HHMMSS> under ${DFTRACER_WORKSPACES:-./workspaces}; then dftracer_pipeline_run_check(result, app, check_files=True) passes.
- [tool:pipeline_get_run_id] 2026-07-06: pipeline_get_run_id(app) after a create returns the active run from <app_name>/.current_run; with no runs it says to call pipeline_create_run.
- [tool:pipeline_list_runs] 2026-07-06: pipeline_list_runs(app) lists runs with created_at, step and is_current; exactly one is current after a create.
