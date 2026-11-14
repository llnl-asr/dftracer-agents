# Lessons: dftracer_pipeline_run

- [L-6008701e] LLM passed a literal '<run_id>' or '{RUN_ID}' as run id and the pipeline blocked -> template strings taken as ids -> _new_run_id substitutes a UTC timestamp; always use the run_id a tool returned (2026-07-06)
- [L-baa44f7a] Steps wrote into hand-built paths such as ws/build_ann and the workspace drifted -> paths built by string concatenation -> get every path from session_get_run_paths / session.json['paths'] and validate structure before annotating (2026-07-06)
