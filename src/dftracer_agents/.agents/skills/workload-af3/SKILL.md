---
name: workload-af3
description: >
  AlphaFold3 (AF3, JAX/XLA inference on ROCm) specific knowledge: model-weight
  default path, smallest sample input, and two real dftracer-annotation build
  bugs found during the annotate-smoke pipeline (cache-decorator ordering,
  ROCm jax_triton patch dead code). Load this skill whenever working with AF3.
---

Cross-references: [[dftracer-annotate-python]] [[dftracer-annotate-general]] [[software-ray]] [[system-tuolumne]]

---

## Facts

### Default model weights directory

AF3's own `_DEFAULT_MODEL_DIR` (defined in `run_alphafold.py`, the `MODEL_DIR`
flag's default value) already points at
`/p/vast1/<af3-weights-group>/alphafold_data/params_af3/` on Tuolumne — no `--model_dir`
override is needed once that path is readable. Read it via its real path
directly (never through a workspace symlink), same rule as the read-only MSA
`.a3m` inputs.

### Smallest available sample input

The sample set at
`/p/vast1/<af3-sample-group>/smol_workflow/pdbbind_casf2016_sample/01_af/` has no
truncated/smaller fixture — `1eby` (18MB MSA) is already the smallest/fastest
structure available, ~110s for a full run (featurization ~25s + inference
~85s across 5 seeds). Use it for smoke tests; don't look for a smaller one.

---

## Annotation Pitfalls — AF3

### `@_dft.log` above `@functools.cache`/`@functools.lru_cache` crashes at import

**Symptom:** `TypeError: unsupported callable` at import time, in files using
`@functools.cache` on MSA/template helper functions (e.g.
`data/pipeline.py::_get_protein_msa_and_templates`,
`model/pipeline/inter_chain_bonds.py`).

**Root cause:** `@_dft.log`'s wrapper calls `inspect.getfullargspec` to build
its call signature. That call fails on a `functools._lru_cache_wrapper`
object — so if `@_dft.log` is placed OUTERMOST (above `@functools.cache`), it
tries to introspect the cache wrapper instead of the original function.

**Fix:** `@functools.cache`/`@functools.lru_cache` must be OUTERMOST,
`@_dft.log` innermost (closest to `def`) — same principle as
`@classmethod`/`@staticmethod` ordering. See
[[dftracer-annotate-python]] Rule 3 (now documents this exact case).

### ROCm `jax_triton/__init__.py` patch was dead code (appended after a `raise`)

**Symptom:** the venv-build step's ROCm triton-capability-probe patch
(`get_compute_capability = None`, `get_serialized_metadata = None`, from the
AF3 README) had no effect — import still hit the original probe.

**Root cause:** the patch was appended AFTER the stock
`try/except AttributeError: raise ImportError` block that guards the
`gpu_triton.get_compute_capability`/`get_serialized_metadata` probe. The
`raise` inside that block aborts module import before the appended override
lines ever execute — dead code by construction, not a patching-tool bug.

**Fix:** REPLACE the stock `try/except AttributeError: raise ImportError`
block directly with the two `None` assignments, don't append after it.
Verify by re-importing (`python -c "import jax_triton"`) and confirming no
`ImportError` — a clean import is not sufficient on its own; also grep the
file to confirm the `try/except` block is actually gone, not just shadowed.

---

## Pipeline session facts (2026-07-29, session af3/&lt;session&gt;)

- Single-process JAX/XLA app — no MPI, no HDF5, `dftracer` installed with
  `DFTRACER_ENABLE_MPI=ON` anyway (Cray MPICH auto-detected) but this does not
  block FUNCTION-mode tracing; see [[bug_hip_tracing_false_positive]]-style
  caution about auto-detected features that don't apply to a given app.
- dftracer's C extension needs Cray CCE runtime libs on `LD_LIBRARY_PATH`
  BEFORE the venv activates, or it silently falls back to a NoOpProfiler
  (zero error, zero trace, process runs to completion normally) — see
  [[system-tuolumne]] and this session's `scripts/env_af3.sh`.
- `dftracer-build-smoke`'s tool set does not always include
  `session_service_start`/`session_service_stop` — if absent, proceed via
  `session_run_smoke_test` alone but explicitly flag the missing node-counter
  daemon bracketing in the step report (pipeline rule 12 still applies at the
  next run that DOES have the tool).
