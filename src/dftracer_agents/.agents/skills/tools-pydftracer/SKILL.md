---
name: tools-pydftracer
description: pydftracer — the Python annotation API package (dftracer.python module, dft_fn/dftracer_fn decorators, initialize_log/finalize). Install verification, common ModuleNotFoundError, and the API surface every python_annotate_file-generated file depends on. Load this skill before any Python annotation build/smoke-test step.
metadata:
  type: software
---

## What it provides

`pydftracer` is a separate PyPI package from `dftracer` (see [[tools-dftracer]])
that supplies the pure-Python decorator API every dftracer-annotated `.py` file
imports:

```python
from dftracer.python import dftracer, dft_fn as DFTracerFn

_dft = DFTracerFn("<category>")
_dft_log = dftracer.initialize_log(logfile=None, data_dir=None, process_id=None)

@_dft.log
def some_function(...): ...

_dft_log.finalize()
```

This is exactly the API `python_annotate_file`/`python_annotate_project`
generate (see `annotation_python.py`) — if this package isn't installed and
importable in the SAME venv the annotated app runs in, every annotated file
fails at import time with:
```
ModuleNotFoundError: No module named 'dftracer.python'
```

## Verification (always do this before running a smoke test on annotated Python)

```bash
python -c "from dftracer.python import dftracer, dft_fn"
```
If this fails, do NOT proceed to the smoke test — fix the install first (see
[[tools-dftracer]] for the install-source options and the "dftracer without
pydftracer" bug pattern). Checking `import dftracer` alone is NOT sufficient —
the C-core package can be present and importable while `pydftracer` (and thus
`dftracer.python`) is missing.

## Install

`pydftracer` is normally pulled in automatically as a `dftracer` dependency
(`pip show dftracer` → `Requires: ... pydftracer ...`). If it's missing despite
that, either:
- the `dftracer` install was incomplete/non-standard (see [[tools-dftracer]]
  bug note) — reinstall `dftracer` cleanly into a fresh session venv, or
- install it explicitly: `pip install pydftracer` (PyPI) or as part of a
  GitHub-develop pip install of the `dftracer` repo, whichever source
  `session_install_dftracer` is using for this session.

## typing_extensions is a hidden runtime dependency

Observed 2026-07-16 (flux-fiction session): `pip install "git+https://github.com/LLNL/dftracer.git@develop"`
succeeds and installs `pydftracer`, but `from dftracer.python import dftracer, dft_fn`
still fails until `typing_extensions` is ALSO installed — it is not currently
declared as a dependency of either package. Install it alongside:
`pip install typing_extensions`. Verify with the import check above, not just
`pip show pydftracer` succeeding.

## OPEN QUESTION — two different decorator API patterns seen across sessions (do not silently paper over)

The `python_annotate_file`/`python_annotate_project` MCP tools generate:
```python
from dftracer.python import dftracer, dft_fn as DFTracerFn
_dft = DFTracerFn("<category>")
@_dft.log
```
But at least one session observed a DIFFERENT pattern already present/expected
in annotated code:
```python
from dftracer.logger import dftracer_fn
@dftracer_fn(comp="cpu")
```
`dftracer.logger` does not exist on the `develop` branch installed 2026-07-16.
**RESOLVED 2026-07-16: `dftracer.python` (`dft_fn`, `dftracer.initialize_log`)
is the correct, current API** — the one `python_annotate_file`/
`python_annotate_project` actually generate. `dftracer.logger`/`dftracer_fn(...)`
was a stale pattern from files annotated by an older manual/AST-fallback pass
that predated (or diverged from) the MCP tool. **Do not write a compatibility
shim to make the old import path resolve** — a shim just hides an
annotation-tool/API drift bug. Fix it at the source: re-annotate the affected
file(s) from pristine source via the MCP tool so they use `dftracer.python`
like everything else, and delete any shim that was added as a stopgap.

## Venv-sharing rule

The venv where `pydftracer` is importable MUST be the same venv the traced
Python app is installed into and run from — never install dftracer's Python
bindings into one venv and run the annotated app from another. See
[[feedback_dftracer_aiml_venv]].
