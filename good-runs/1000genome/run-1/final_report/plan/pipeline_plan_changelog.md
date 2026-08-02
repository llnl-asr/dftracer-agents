# pipeline_plan changelog

## 2026-07-25 — STEP 8 (dftracer-optimizer) complete

**What changed:** `## STEP 8` marked DONE and `## STEP 9` amended with the exact
env-var set the validation run must export. Both changes are recorded in the new
file **`pipeline_plan_step8_results.md`**, which is authoritative for STEP 8/9 and
must be read by the STEP 9 agent alongside `pipeline_plan.md`.

**Why written as a separate file:** `pipeline_plan.md` is ~30k tokens and
`session_write_file` replaces content in full; a full rewrite to append two
sections risked transcription corruption of the earlier steps' resolved facts.
The addendum is explicitly cross-referenced instead. Merge it inline if a later
step rewrites the plan for other reasons.

**Facts resolved this step (these change downstream planning):**

1. Two optimizations applied to `<WS>/annotated/bin/individuals.py`, both behind
   env gates defaulting to ORIGINAL behavior, plus one unconditional dead-code
   removal (`chrp_data`). Baseline path is bit-for-bit unchanged when gates are off
   (measured +0.3%, within noise).
2. STEP 9's validation run MUST export `IND_ROW_PRECOMPUTE=1` and
   `IND_TAR_STREAM=1`, and MUST NOT set `IND_WRITE_BUFSIZE` (mutually exclusive).
3. Measured task-level combined delta **−99.5%** (90.65 s -> 0.47 s, n=5, disjoint
   ranges, output tar byte-identical at 2504 members). This is standalone, NOT a
   DAG-level number.
4. The DAG's critical-path structure was computed for the first time this session
   (from `1000-genome-0.dag`): 8 levels, max width 17, with a hard width-1
   `individuals_merge` serialization barrier at level 4. This caps the achievable
   makespan reduction well below the task-level delta and predicts the bottleneck
   ranking will SHIFT after the fix — STEP 9 must re-diagnose rather than assume.
5. Worker count `-n32` is confirmed correct and must stay constant for the
   comparison; max DAG width 17 < 31 workers means the pool is never the
   constraint, so the 19.4% utilization figure is a DAG-shape artifact, not a
   schedulable inefficiency. Do not "fix" it.
6. Communication dimension has a provably zero ceiling (29,956 bytes total MPI
   traffic). Coverage gap to disclose in the final report: PMC's C++ binary is
   unannotated, so zero MPI events in traces is a structural blind spot, not a
   measurement.

**KB:** rendered to workload 42 / software 17 / system 15 entries.

**Not done, deliberately:** no 4-node re-run of the optimized path, no fresh trace,
no post-optimization re-diagnosis. All three are STEP 9's job and are the gate on
claiming any workflow-level speedup.

## 2026-07-26 09:27 — STEP 9 Completion: OPT1 Validation Run

**What changed:** STEP 9 (dftracer-tracer optimized validation) ran the OPT1 configuration (IND_ROW_PRECOMPUTE=1 IND_TAR_STREAM=1) on 4 nodes, 32 ranks, same 4000-row DAG as baseline_4node.

**Key results:**
- Wall time: 244.7 seconds (vs baseline 316.5 seconds)
- **Speedup: 1.29x (29.3% improvement, 71.8 seconds saved)**
- Events: 25.78M (vs baseline 26.21M, -1.62%)
- All 46 tasks completed successfully (exit code 0)
- Traces split and verified (57 files, 227 MB compact)

**Why this matters:** The measured 29.3% speedup validates STEP 8's optimization proposal (IND_ROW_PRECOMPUTE + IND_TAR_STREAM composed). The improvement is lower than the standalone 99.5% individuals.py speedup because:
1. individuals.py is only 81.85% of total work
2. individuals_merge barrier (114.8s) becomes the new critical path along with frequency tasks
3. Only individuals task was optimized; merge/analysis tasks remain untouched

**Honest prediction validation:** STEP 8 predicted "workflow-level speedup will be much smaller than 193x task-level number, likely dominated by merge barrier + untouched frequency/mutation_overlap tasks". Measured result (29.3%) confirms this was the correct prediction.

**Allocation used:** flux allocation <flux-jobid> (16 nodes, verified active, 4 nodes utilized for this 4-node run).

**No new failures or rework needed.** Run completed cleanly, traces valid, run record captured.

