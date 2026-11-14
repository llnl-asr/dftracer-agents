# What worked: dftracer_provenance_annotate

## elmerfold/provenance/annotate
Python, three process kinds (driver, process pool, GPU server). Init once per pid in the pool initializer and in the server's startup; finalize with `atexit.register(fini)` and `multiprocessing.util.Finalize(None, fini, exitpriority=100)`. Pool tasks wrapped: `f` renamed `_f_impl`, same-named wrapper opens the activity. RPC edges: subclass `ServerTask.start` / `SubprocessTask.on_finish` for the driver side, the request handler records the server side with the identical type+id. Run with `DFTRACER_ENABLE=1 DFTRACER_INC_METADATA=1`. 240 entities / 181 activities, 0 dangling (2026-10-01).

## tool:provenance_insert
One call per statement group at a 1-based line, `position="after"` to indent one level after `{` / `:`; re-read the file after a batch (line numbers shift). Re-running is a no-op (dft-prov marker).

## tool:provenance_ensure_lifecycle
C/C++: call on the file that defines `main`, with `compile_flags` holding absolute `-I` paths when it includes `mpi.h` or other non-system headers.

## tool:provenance_install_helpers
`include_into` lists the files to receive `import dftracer_prov as prov` / `#include "dftracer_prov.h"`; re-run after any spec change so the type registry matches.
