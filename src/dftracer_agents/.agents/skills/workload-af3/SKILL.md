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

## Optimization findings (2026-08-02)

### Inference wall time is quadratic in PADDED BUCKET token count, not "MSA depth"

A baseline diagnosis initially attributed `run_inference`'s 14.1x wall-time
scaling (across a 4.44x MSA-size increase) to "MSA depth feeding the model's
attention/sequence dimension". Deeper analysis of AF3's own `absl` logs
(`Calculating bucket size for input with N tokens`, `Running model inference
with seed X took Ys`) refuted that framing: the real driver is that
steady-state per-seed inference time scales as **N_bucket^2.02** — the
padded (bucket-rounded) token count, not the raw MSA row count — plus a
fixed **~32-43s one-time XLA JIT compile cost per process** (33.8% of a
small ~110s run's wall time, only ~4.0% of a large ~1090s run's). MSA file
size correlates with token count (longer proteins → bigger `.a3m` files),
but the causal lever is bucket size + compile, not MSA parsing/depth per se.
**When decomposing an AF3 run's cost, always separate seed 1 (compile + run)
from seeds 2+ (steady state) using the absl log timestamps before attributing
scaling to any other mechanism** — no dftracer re-annotation of the model's
internal stages (Evoformer/diffusion) is needed for this decomposition, AF3's
own logging already suffices.

**Bucket padding is a real, size-dependent waste** (e.g. a 1085-token input
computed at a 1280 bucket = ~18% wasted work), but a bucket must stay a
multiple of 64/128 for GPU tiling — a non-aligned "tighter" bucket (tested:
248 vs the default 256, at 1eby scale) LOSES more to tiling inefficiency than
it saves in tokens and is a net no-op/regression. Only propose a tighter
bucket when it's still tile-aligned and the padding fraction is large.

**CONFIRMED WIN at 1e66 scale (2026-08-02, single replicate, allocation-limited):**
replacing the default bucket list's `1280` entry with a 64-aligned `1088`
(`--buckets=256,512,768,1024,1088,1536,2048,2560,3072,3584,4096,4608,5120`,
vs default `256,512,768,1024,1280,1536,2048,2560,3072,3584,4096,4608,5120`) —
**~27% wall-time reduction**: total featurize+inference dropped from
1077-1097s (two baseline replicates) to **793s**. Steady-state per-seed
inference dropped from ~182-186s to **~125.5s** (ratio 0.68, reasonably
close to the (1088/1280)^2.02 ≈ 0.72 predicted by the quadratic-in-bucket
law — actual came in slightly better than the naive prediction). Correctness
preserved: `ptm`/`iptm`/`ranking_score` (0.66/0.80/0.77) fell within the same
range as two baseline runs (0.65/0.79/0.76) and the run-to-run GPU
nondeterminism already established for this app — no systematic
degradation. **This is the single largest confirmed win in the AF3
optimization pass** — larger than the memory `PREALLOCATE=false` result,
and it holds at exactly the scale (large-MSA, GPU-inference-dominated) where
that result did not transfer. Zero source-code changes, one CLI flag.
Caveat: single replicate only (allocation-limited) — a second 1e66 replicate
would strengthen the noise-band claim, but the effect size (~27%) is far
outside the ~2% noise band already established from the two existing
baseline replicates, so this is a high-confidence result even at n=1.

**The `XLA_PYTHON_CLIENT_PREALLOCATE=false` memory win does NOT transfer to
1e66 scale (confirmed, 2026-08-02):** measured 1109s at 1e66 vs the
1077-1097s baseline replicate range — no improvement, if anything a slight
(within-to-just-outside-noise) regression. This matches the mechanism
prediction: the win at 1eby came from relieving CPU/featurization memory
pressure, which is a shrinking fraction of wall time as MSA size grows
(9.9% of 1e66's wall vs 20.3% of 1eby's), while GPU inference (which this
env var doesn't meaningfully affect) dominates at 1e66. **Do not carry the
1eby-scale memory win into a 1e66-class production recommendation** — it is
real but scale-limited.

### Correctness must be checked via `ptm`/`iptm`, never a CIF byte-diff

AF3 is **run-to-run nondeterministic on GPU regardless of configuration** —
re-running the identical baseline twice produces a `<id>_model.cif` that
differs by thousands of lines from itself, the same magnitude as the diff
between a baseline and a genuinely different configuration. A CIF byte-diff
is therefore not a valid correctness check for any AF3 optimization. Use
`<id>_summary_confidences.json`'s `ptm`/`iptm` (and `ranking_score`) instead —
these were confirmed bit-stable (0.92/0.78) across 6 different
configurations in one optimization pass while the CIF never matched once.
Note: an `--xla_gpu_autotune_level` change DID perturb `ranking_score` in its
second decimal (0.81→0.80) — autotuning is not perfectly numerically neutral,
worth knowing for a correctness-sensitive comparison.

### `--flash_attention_implementation=triton` silently falls back to `xla` on ROCm

Runs cleanly, produces identical timing to `xla`, and raises no error or
warning — it is NOT actually using a different kernel. Root cause: the
triton flash-attention path requires Ampere+ (NVIDIA), and this venv's
`jax_triton.get_compute_capability`/`get_serialized_metadata` are nulled out
by the AF3 ROCm patch (see the annotation-pitfalls section above) precisely
so the probe doesn't hard-fail on ROCm — but that means selecting `triton`
here is a silent no-op, not a working alternate kernel. Confirmed at
jaxlib 0.4.34 + jax_rocm60 + ROCm 6.0.0; do not credit a `triton` flag change
without independently proving kernel selection (e.g. via profiler kernel
names), a timing match alone is not sufficient evidence it engaged.

### XLA persistent JIT compilation cache crashes on cache-hit with `HIP_ERROR_OutOfMemory`

Setting `--jax_compilation_cache_dir` populates the cache correctly (confirmed
via file `atime` on reuse — a genuine cache hit, not a miss), but the
subsequent `run_inference` call then crashes: `RESOURCE_EXHAUSTED: Failed to
instantiate HIP graph: HIP_ERROR_OutOfMemory`. This would otherwise reclaim
the ~32-43s fixed JIT compile cost on every subsequent process launch — a
potentially large win (33.8% of a small run's wall time) if fixed. Untested
workaround suggested by the error text itself: add
`XLA_FLAGS=--xla_gpu_enable_command_buffer=` (disables the HIP command-buffer/
graph path implicated in the crash). Confirmed on jaxlib 0.4.34 + jax_rocm60 +
ROCm 6.0.0 — verify the workaround before relying on it.

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
