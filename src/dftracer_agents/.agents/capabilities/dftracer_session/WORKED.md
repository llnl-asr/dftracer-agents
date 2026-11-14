# What worked: dftracer_session

What finally worked, portable (`${WS}` = the session workspace, `${URL}`/`${REF}` = the app's repository and ref).

- [tool:session_create] `session_create(url=${URL}, ref=${REF})` then `profile_bind(run_id, app, system)` at once; the clone lands in `${WS}/source` and is copied (symlinks preserved, dangling links ignored, submodules initialised) into `${WS}/baseline/source` and `${WS}/annotated/source`; `dftracer_session_check(run_id)` passes.
- [tool:session_import_local_tree] for a tree already on disk (no git URL): import it into a new session instead of cloning; the import records `imported_from` in session.json.
- [tool:session_validate_structure] before annotation and before/after every optimization iteration; `clean: false` -> `session_reorganize_structure(run_id, dry_run=False)` (quarantines drift under `artifacts/legacy/`, deletes nothing).
- [flashx/any/session_create] 2026-07-07: Flash-X clones only with the symlink-preserving copy (dangling TurbGen.h link).
- [rajaperf/any/session_create] RAJAPerf configures only after the recursive submodule update the tool performs.
