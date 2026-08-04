---
name: tuolumne-ior-run-with-preload-sanitize-env
description: When running dftracer-preload under this harness, LD_PRELOAD/DFTRACER_* are pre-set by the harness; run IOR with `env -u ... LD_PRELOAD=<session libdftracer_preload.so> ...` and prepend to (not replace) `LD_LIBRARY_PATH` so PMI libs resolve.
metadata:
  type: feedback
---

**Why:** Some harnesses pre-export `LD_PRELOAD` and `DFTRACER_*` for tracing the agent itself. That state leaks into `session_run_with_dftracer` runs (especially under `flux proxy`), causing (a) dftracer to write traces to the agent-run directory instead of the session workspace, and (b) missing dependencies when `LD_LIBRARY_PATH` is overwritten (notably `libpmi.so.0`).

**How to apply:** In the run wrapper, prepend needed libs instead of overwriting: `export LD_LIBRARY_PATH="$DFTRACER_LIB64:$HDF5_LIB:$LD_LIBRARY_PATH"`. Launch the application with a clean env and explicit preload via `env -u LD_PRELOAD -u DFTRACER_INIT -u DFTRACER_LOG_FILE -u DFTRACER_ENABLE -u DFTRACER_INC_METADATA -u DFTRACER_DATA_DIR LD_PRELOAD=<session>/libdftracer_preload.so DFTRACER_ENABLE=1 DFTRACER_INIT=FUNCTION DFTRACER_INC_METADATA=1 DFTRACER_LOG_FILE=<ws>/<run>/traces/raw/<name> DFTRACER_DATA_DIR=all <app> ...`.
