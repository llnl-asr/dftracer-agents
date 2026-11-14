# What worked: dftracer_trace

Portable recipes (`${WS}` = session workspace, `${PFS}` = the system-detected parallel file system, `<alloc>` = the user's allocation).

- [tool:session_run_with_dftracer] `session_init_run(run_id, "baseline")`; `session_service_start(run_id)`; `session_run_with_dftracer(run_id, command="<abs binary> <args with output under ${PFS}>", run_name="baseline", data_dir="all", allocation_id="<alloc>", nnodes=N, ntasks=N*48)`; `session_service_stop(run_id)`; `session_split_traces(run_id, "baseline")`; `dftracer_trace_check(run_id, "baseline", expected_ranks=N*48, require_compact=True)` passes.
- [flashx/production/run] `flux proxy <alloc> flux run -N <n> -n $((n*48)) --exclusive ./production_run.sh` where the script loads the app's modules and exports LD_LIBRARY_PATH, DFTRACER_ENABLE=1, DFTRACER_INIT, DFTRACER_DATA_DIR=all, DFTRACER_INC_METADATA=1, DFTRACER_LOG_FILE=${WS}/baseline/traces/raw/baseline/baseline, LD_PRELOAD (PRELOAD mode), HDF5_USE_FILE_LOCKING=FALSE.
- [any/any/split] `dftracer_split -d <raw> --output <split> --index-dir <split>/idx --compress --app-name <run>` then analyse `<split>` (2026-07-08).
- [any/any/service-pinned] `flux submit -N4 -n4 -c1 --requires=host:<a>,<b>,<c>,<d> --setattr=exclusive=false "$DFT_BIN" start "$SVC_DIR"`; app with the same `--requires`; stop the same way.
- [tool:session_search_papers_for_config] `session_search_papers_for_config(run_id, app_name="Flash-X", problem_name="Sedov")` before a production run; results persisted to session.json and artifacts/paper_config_search.json.
