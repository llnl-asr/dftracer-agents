---
name: bug-python-annotate-multiline-import-fixed
description: FIXED at tool level: python annotation inserted the dftracer import inside an open multi-line parenthesized import, corrupting files — recurred across two sessions before being fixed
metadata:
  type: project
---

`_last_import_idx()` in `src/dftracer_agents/mcp_tools/tools/session/annotation_python.py` (also used by `annotation_ai.py`) decided where to insert the `from dftracer.python import ...` line by scanning lines with a regex. For a multi-line import:

```python
from megatron import (
    get_args,
)
```

it matched the OPENING line and returned the next index — i.e. **inside the parentheses** — producing an immediate `SyntaxError` and a corrupted source file.

**Impact:** hit ~3 files per session on Megatron-DeepSpeed, in BOTH the GPT and BERT sessions, each time requiring manual relocation of the import. Worse, the annotating subagent reported success anyway, so it was only caught by independent `ast.parse` verification.

**Fix (applied):** rewrote `_last_import_idx` to parse the source with `ast` and use each top-level `Import`/`ImportFrom` node's real `end_lineno`, so multi-line statements are respected. Added `_last_import_idx_fallback` (paren/bracket-depth aware) for sources that do not parse. Also handles the no-imports case by inserting after the module docstring rather than above it (which would silently demote the docstring to a bare expression).

Verified against 5 cases — multi-line paren import, simple imports, docstring-only, imports inside `try`, conditional import — all now produce parseable output; previously the first case failed.

**Why it matters beyond the crash:** this is the class of bug where the tool damages the file and the agent reports success. Independent `ast.parse` verification after annotation is what caught it both times; keep that check.

See [[bug-annotator-fabricated-report]], [[software-megatron-deepspeed]].
