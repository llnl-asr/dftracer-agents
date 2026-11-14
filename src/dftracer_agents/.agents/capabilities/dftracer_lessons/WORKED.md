# What worked: dftracer_lessons

## tool:session_lessons_sync_preview
Always first: it diffs the staged annotation lessons (workspace copy) against the repo copy and lists the new entries; read-only with a local checkout.

## tool:session_lessons_sync_pr
Only after the user approved the previewed entries, with GITHUB_TOKEN/GH_TOKEN set and no unrelated uncommitted changes in the local checkout: `session_lessons_sync_pr(run_id, confirm=True)` returns pr_url, branch, entry_count.

## annotation/c/build-system
Patch the build system BEFORE annotating: `-I<install>/include` in CFLAGS, `-L<install>/lib -Wl,-rpath,<install>/lib` in LDFLAGS, `-ldftracer_core -lcpp-logger -lbrahma` in LIBS (autotools: patch src/Makefile too), then `rm -rf .deps src/.deps && make -j4`. Run with `DFTRACER_INIT=FUNCTION` when the source has explicit DFTRACER_C_INIT.

## annotation/c/mpiio
Annotate MPI-IO style backends last, one function at a time, simplest first (MPIIO_Close, MPIIO_Delete), reading the whole body first and building after every function; END before every return and before error-checking macros; a single END at a goto label.
