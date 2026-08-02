---
name: feedback-dftracer-aiml-venv
description: "For AI/ML Python apps, dftracer and the app must share the same venv; FUNCTION mode works correctly for forked DataLoader workers"
metadata: 
  node_type: memory
  type: feedback
---

**Canonical home:** see the `tools-pydftracer` and `dftracer-ml-annotate` skills, and
`workload-scaffold` (shared-venv pattern applied end-to-end).

# dftracer AI/ML venv and FUNCTION mode

For AI/ML Python apps, dftracer must be installed into the **same venv as the app** (`ws/install/`). Do NOT create a separate `ws/venv/` for dftracer and then copy dftracer into the app venv post-hoc.

**Why:** `import dftracer` must resolve from the app's own Python environment at runtime. Copying site-packages is fragile (breaks on upgrades, misses .pth files, causes version skew). The correct approach is to `pip install dftracer` directly into the app's venv during `session_install_dftracer`.

**How to apply:** In `session_install_dftracer`, detect the app's venv path (`ws/install/`) and run `pip install dftracer` there. Never create a parallel `ws/venv/` for Python/AI/ML projects.

---

## FUNCTION mode and forked DataLoader workers

In FUNCTION mode, dftracer initializes in each PyTorch DataLoader worker on fork and finalizes when the worker exits cleanly. Worker I/O **will** appear in traces under normal conditions — no need to switch to HYBRID just for DataLoader coverage.

If worker I/O is missing: the workers exited uncleanly (SIGKILL, OOM, uncaught exception). Diagnose via stderr, reduce num_workers, or check memory limits.

**Do NOT recommend HYBRID mode as a fix for missing DataLoader I/O** — it is only needed if workers are crashing uncleanly and FUNCTION mode can't recover from that anyway.

See [[feedback-flux-proxy-wrapper]].
