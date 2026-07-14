---
name: feedback-always-function-mode
description: Always use dftracer FUNCTION mode (source annotation) — never fall back to PRELOAD/LD_PRELOAD interception
metadata:
  type: feedback
---

Always integrate dftracer via FUNCTION mode — i.e. actual source-level annotation with `DFTRACER_C_FUNCTION_START`/`END` (or language-equivalent) macros compiled directly into the app — never fall back to PRELOAD/`LD_PRELOAD`-based interception as the tracing mechanism, even when it looks like a convenient workaround (e.g. to sidestep a build/link/ABI issue).

**Why:** User stated this as a standing rule. FUNCTION mode gives accurate, code-level function boundaries and lets annotation carry `comp=` tags and app metadata; PRELOAD mode only intercepts library-call boundaries and loses that semantic richness.

**How to apply:** Every `dftracer-annotator` / `dftracer-annotate-cpp` / `dftracer-annotate-c` / `dftracer-annotate-python` step must produce a source-annotated build via `session_build_annotated`, and every `dftracer-tracer` / `dftracer-optimizer-*` run step must run that annotated binary directly (no `LD_PRELOAD=libdftracer_preload.so` substitution). If a build/link issue (e.g. an ABI mismatch) makes FUNCTION-mode integration seem harder, fix the underlying build issue — don't route around it with PRELOAD mode. See [[bug-dftracer-crayclang-python-abi]] (that issue is about the Python module, unrelated to and not a reason to avoid FUNCTION-mode C++ annotation).
