---
name: feedback-app-uses-real-pfs-path-not-symlink
description: Applications must open data via the REAL PFS path, never the workspace dataset symlink — traces record the opened path and the symlink hides which file was actually touched
metadata:
  type: feedback
---

Session data must live on the parallel filesystem (Lustre), and `<WS>/dataset` is a symlink to it. But the APPLICATION must be given the **real PFS path** (e.g. `/p/lustre5/.../<dataset>`), NOT the `<WS>/dataset/...` symlink path.

**Why:** dftracer records the path the application actually opens. If the app opens files through the symlink, every trace event shows the workspace path instead of the real file. You then cannot tell which physical file was accessed, cannot attribute I/O to the correct filesystem, and the entire I/O analysis becomes ambiguous — which defeats the purpose of the tracing run.

**How to apply:** use the resolved `/p/lustre5/...` path in `--data-path`, `--vocab-file`, config files, run scripts, and the app's own output/checkpoint directories. Resolve it with `readlink -f "<WS>/dataset"` if needed. The `<WS>/dataset` symlink remains useful for humans browsing the workspace — just never hand it to the application.

Unchanged: dftracer TRACES stay in the session workspace under `<WS>/<run>/traces/`, never on the PFS. See [[feedback-lustre-io]], [[feedback-optimization-pipeline-traces]].

**Related bug:** `session_create(dataset_path=...)` creates the `dataset` symlink but does NOT create its target directory, leaving it dangling. Verify with `ls -ld "$WS/dataset/"` and `mkdir -p` the target before use, or writes fail (or silently land somewhere unintended). Worth fixing in the tool.
