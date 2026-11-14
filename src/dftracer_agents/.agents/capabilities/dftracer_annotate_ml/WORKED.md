# What worked: dftracer_annotate_ml

What successful annotation runs finally used (portable). One entry per key, newest wins.

- [tool:ml_annotate_project] fast path: ml_annotate_plan(run_id, threshold=20) to review, ml_annotate_project(run_id, threshold=20) to execute (categorize, cost gate with the dft_ai exemption, python_annotate_ai_file for semantic buckets, python_annotate_file for the rest, validate=True), then annotate_add_app_metadata on the entry file and validate_annotations(language="python"). (dftracer-ml-annotate STEP 4 FAST PATH)
