---
name: project-alphafold3-optimization
description: AF3 inference dftracer pipeline on Tuolumne — STEPS 1-4 done and verified; BLOCKED at smoke test on model-weights group access
metadata:
  type: project
---

AlphaFold3 (AF3) inference annotate+optimize pipeline on Tuolumne. Requested to profile
AF3 rather than OpenFold because AF3 is more widely used at the site.

**Status: STEPS 1-4 complete and verified. BLOCKED at STEP 5 (smoke test) on model-weights
group access.**

## Blocker
AF3 model weights live at a group-restricted path (`_DEFAULT_MODEL_DIR` hardcoded in
`run_alphafold.py`, pointing at an `alphafol`-group tree). Session user is in the
workflow group but NOT the weights group -> `Permission denied`. AF3 inference cannot
run at all without weights. Requires an IDM group-access request, per the AF3 README.
No workaround: weights are licence-restricted and must not be copied.

## Completed and verified
1. Session created; profile bound.
2. Venv rebuilt from the AF3 README into the session workspace (NOT the shared group
   venv). Verified: Python 3.11.13, jax 0.4.34, `jax.devices()` -> 4 ROCm devices,
   `import alphafold3` OK. See [[software-alphafold3]] for the full recipe.
3. dftracer installed into that SAME venv. Required fixing a silent-failure bug — see
   [[bug-dftracer-cray-runtime-silent-noop]]. Verified working: real 80-event `.pfw.gz`.
4. Annotated 25 files under `src/alphafold3/` + `run_alphafold.py`. `compileall` exit 0
   with the venv Python; lint/dedup clean. Editable install re-pointed at the annotated
   tree and VERIFIED (`import alphafold3` resolves under `annotated/source/`, module
   source contains decorators, pybind C++ extensions still import).

## The performance question (this is the point of the study)
The scientist expects MSA search to dominate. But in the configuration provided, MSAs are
**pre-computed** — input JSONs carry `unpairedMsaPath`/`pairedMsaPath` to cached `.a3m`
files, so the search never runs. Measured from a prior uninstrumented run log, wall time
tracks MSA **file size**, not GPU work:
`~18 MB -> ~181-198 s`, `~27 MB -> ~426-430 s`, `~80 MB -> ~2790 s`, while
"model inference with 5 seeds" stayed ~146 s throughout.

**Hypothesis to confirm/refute with traces:** the dominant cost is reading/parsing/
featurising the large `.a3m` files on the CPU data path, not diffusion inference.
Annotation was deliberately aimed at `data/`, `parsers/`, `model/pipeline/` plus
top-level phase boundaries to split runtime into read / data-prep / inference / write.
Note the real parsing work is in pybind11 C++ (`alphafold3.cpp.fasta_iterator`,
`msa_conversion`); the annotated Python wrappers bracket those calls, and POSIX
interception captures the underlying reads, so C++ annotation was deliberately skipped.

## Plan once unblocked
Baseline = small+large pair (smallest ~18 MB MSA structure, and the ~80 MB outlier) to
isolate MSA-size scaling, then analyze -> diagnose -> all four optimizer dimensions ->
report -> privacy guard.

## Constraints for this project
Shared group trees (workflow scripts, sample data, MSA db, shared venv, weights) are
strictly READ-ONLY. All writes go to the session workspace or the user's own PFS scratch.
The stock runner's `OUTPUT_DIR` points into the read-only sample dir and must be
redirected. See [[feedback-write-only-in-user-space]].

Related: [[software-alphafold3]], [[bug-dftracer-cray-runtime-silent-noop]],
[[feedback-pydftracer-api-import-path]], [[system-tuolumne]].
