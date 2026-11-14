# What worked: dftracer_annotate_ai

What successful annotation runs finally used (portable). One entry per key, newest wins.

- [tool:python_annotate_ai_file] per-file repair inside the ML fast path: python_annotate_ai_file(run_id, filepath, category=<entry|train|data|ckpt|comm>, is_entry=...) then python_write_ai_file; dft_ai regions are exempt from the cost gate. (dftracer-ml-annotate STEP 4 manual)
