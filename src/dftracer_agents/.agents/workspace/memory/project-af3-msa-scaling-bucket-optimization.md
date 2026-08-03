---
name: project-af3-msa-scaling-bucket-optimization
description: AF3 (AlphaFold3) MSA-size-scaling diagnosis + XLA bucket-alignment optimization on Tuolumne -- pipeline complete, -27% wall time CONFIRMED via real GPU-allocation validation (831s reproduced vs 793s original), final_report validated=True and privacy-clean
metadata:
  type: project
---


Session `af3/<session>` -- AlphaFold3 JAX/XLA inference (single-process, single-node,
MI300A APU, no MPI/HDF5). Full annotate/trace/diagnose/optimize/report pipeline
complete, INCLUDING a real GPU-allocation self-contained validation of
`final_report/` (not just structural/syntax checks).

**The question:** scientist believed CPU-side MSA parsing/featurization
dominates wall time for large-MSA structures with GPU inference ~constant.
**Answer: both halves refuted/corrected.** GPU `run_inference` (5 seeds) scales
14.1x for a 4.44x MSA-size increase (super-linear, not constant), growing from
71.0%->87.7% of wall time; CPU featurization scales sub-linearly (5.5x) and
SHRINKS as a fraction of wall time (20.3%->9.9%). True mechanism (found via
AF3's own absl JIT-compile logs, not the trace): GPU cost is quadratic in the
PADDED XLA BUCKET TOKEN COUNT (N^2.02) plus a fixed ~32-43s per-process JIT
compile cost -- MSA size only correlates with token count, isn't itself causal.

**Headline result, now VALIDATED on real hardware: -27% wall time at 1e66
scale.** Original measurement: 793s vs 1077-1097s baseline. Re-validated
standalone from `final_report/` alone (GPU alloc, pdebug, single MI300A node,
~15 min run): **831s reproduced** (within the same ~750-850s band, still far
below the 1077-1097s baseline range) with **exact correctness match**
(ptm=0.66/iptm=0.80/ranking_score=0.77, identical to the original measurement).
Zero source changes, one CLI flag:
`--buckets=256,512,768,1024,1088,1536,2048,2560,3072,3584,4096,4608,5120`
(swaps the app-computed default 1280 for a still-64-tile-aligned 1088).

A second win, `XLA_PYTHON_CLIENT_PREALLOCATE=false` (-4.9% wall/-77% peak mem
at 1eby, 3 reps/side, disjoint ranges), did NOT transfer to 1e66 (no measurable
improvement) -- real but scale-limited, correctly not credited at the scale
that matters.

I/O and communication dimensions were both walked through their full
checklists (pipeline rule 14) and correctly declined everything -- I/O is
0.10-1.2% of wall time with IDENTICAL POSIX op counts across MSA scales;
communication is structurally zero (single process, verified via source +
trace pid/tid count, not just an empty trace).

**Unmeasured/next-step candidates (allocation ran out during STEP 8, and the
validation-round allocation was single-purpose for the headline result only):**
JIT persistent compile cache (crashed on cache-hit with `HIP_ERROR_OutOfMemory`,
untested workaround `--xla_gpu_enable_command_buffer=`); 4-GPU concurrent seed
execution (highest weighted score, 67.5, never run); two orphaned memoization
candidates (`extract_msa_features` across seeds ~1.7% ceiling, `mol_from_ccd_cif`
~0.45% ceiling).

**Reconfirms (3rd known instance):** `mcp__dftracer__comparator` is broken for
Python FUNCTION-mode/`DFTracerFn` traces -- see
[[bug-dftracer-comparator-fails-ray-molformer]].

**New AF3-class correctness rule:** a CIF/output byte-diff is NOT a valid
correctness check when the app is run-to-run nondeterministic on GPU regardless
of config -- use the app's own confidence metrics (ptm/iptm/ranking_score)
instead; confirmed twice this session (original + validation run matched
exactly).

**Two real tool-level bugs found and worked around during the final-report
validation pass (PROPOSED for confirmation, not yet fixed at the tool level):**
1. `session_final_report`'s automatic script anonymization rewrote the
   session's real, non-personal, site-shared read-only data path
   (`$VAST_ROOT`, holding AF3's MSA db + input JSONs) to an undefined
   `$VAST_ROOT` in every hand-added run script, with no corresponding
   definition added anywhere -- this silently broke every hand-added script
   with a `FileNotFoundError` the first time one was actually executed.
   Workaround: add `VAST_ROOT=` as a `config.ini` placeholder (same pattern as
   `WORKSPACE_ROOT`) that the user fills in, exactly like every other real
   value in that file.
2. **`session_final_report` unconditionally regenerates `install.sh` and
   `run_all.sh` from its own generic boilerplate template on EVERY call**,
   silently discarding any hand-written customization from a previous call
   (confirmed across 3 separate re-invocations in this session) -- and the
   regenerated `install.sh` boilerplate itself leaks the real absolute
   workspace path in a comment, while the regenerated `run_all.sh` bakes the
   literal `alloc_hint` (a real flux jobid) in as the default `$1`. Also,
   every call regenerates `config.ini`/`plan/*.md`/`PERFORMANCE.md`/
   `performance/mlflow.json` fresh from live session state, UNDOING any prior
   `privacy_redact` pass on those files. Practical workaround this session:
   do all custom script edits AND the final `privacy_redact` pass strictly
   AFTER the LAST `session_final_report` call, never before -- call the tool
   until the four gates (`pdf.generated`/`completeness.ok`/`readme_check.ok`/
   `validated`) are true, then make no further `session_final_report` calls;
   apply manual script fixes + a final `privacy_scan` (not `_redact`, which
   also seems to only touch flagged files and is safe to call standalone)
   as the true last step. This ORDERING CONSTRAINT is the key operational
   lesson: `session_final_report` is not idempotent with respect to hand-added
   scripts once you've customized them.

**Reconfirmed (again) that `privacy_redact` itself IS safe/idempotent** when
called without an intervening `session_final_report` call -- it only rewrites
files it actually finds flagged content in, leaving already-clean files alone.
The clobbering was specifically from `session_final_report`'s own regeneration
logic, not from `privacy_redact`.

final_report/ assembled with 9 hand-added run scripts (`run_env_af3.sh`,
`run_baseline_{1eby,1e66}.sh`, `run_compute_opt1.sh`,
`run_opt_memory_noprealloc.sh`, `run_verify_1e66_{bucket<node>,noprealloc}.sh`,
`run_proxy_validate_bucket1088.sh` -- the last auto-discovered after use)
beyond the tool's auto-discovered `install.sh`/`run_all.sh` -- confirms the
report-agent instruction to grep the workspace and hand-add any run the
tool's glob misses. `pdf.generated`, `completeness.ok`, `readme_check.ok`,
and `validated` all true, `validation_notes` recorded on the tool's own
`session_final_report` call. Real GPU-allocation validation performed (not
just structural) -- `run_verify_1e66_bucket1088.sh` executed standalone from
`final_report/` alone via a `flux proxy <alloc> flux run -N1 --exclusive
<wrapper>` launch, reproducing the headline result within noise band and with
exact correctness match. privacy_scan clean on final_report/ as the true
final state (31 files scanned, 0 findings) after the ordering-constraint
workaround above.
