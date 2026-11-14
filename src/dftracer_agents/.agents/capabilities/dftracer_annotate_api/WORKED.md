# What worked: dftracer_annotate_api

What successful annotation runs finally used (portable). One entry per key, newest wins.

- [c/annotate/init-args] 2026-07-16: DFTRACER_C_INIT(NULL, NULL, NULL) (third argument int*, NULL or &pid); the old default -1 was fatal under Cray clang -std=c99. clang_annotate_file's init_args default changed accordingly. (dftracer-annotation-lessons)
- [tool:dftracer_get_init_fini] returns the exact init/fini signature, placement notes and doc URL per language (c, cpp, python); use it instead of searching the docs.
