---
name: workload-1000genome
description: >
  1000genome-workflow (pegasus-isi/1000genome-workflow) specific knowledge:
  Pegasus/PMC execution architecture, pure-Python task scripts, annotation
  pitfalls, and run sizing for the dftracer pipeline. Load this skill
  whenever working with 1000genome-workflow.
---

Cross-references: [[dftracer-software-pegasus]] [[software-pegasus]] [[dftracer-annotation-lessons]] [[flux-alloc]]

---

## What this workflow is

A Pegasus scientific workflow analyzing 1000 Genomes Project VCF (Variant
Call Format) data — SNP frequency filtering, SIFT scoring, and pairwise
mutation-overlap analysis across individuals/populations. Unlike Montage
(compiled C, single binary type), every task here is a standalone Python
script under `bin/`:

- `individuals.py` — **dominant cost, 81.85% of baseline wall time** per the
  README. Parses VCF files by chromosome, filters SNPs by allele frequency,
  writes one output file per individual, tars the results.
- `individuals_merge.py` — merges per-job tar.gz chunks for a chromosome.
- `sifting.py` — reads VEP (Variant Effect Predictor) score files, writes
  scored SNP lists.
- `mutation_overlap.py` — untars chromosome data, reads population CSVs,
  computes pairwise SNP overlap between individuals, writes PNG heatmaps.
- `frequency.py` — random-sampling overlap-frequency analysis, matplotlib
  PNG output.
- `individuals_mpi.py` / `individuals_merge_mpi.py` — MPI variants, not part
  of the default PMC task set; left unannotated unless specifically needed.
- `daxgen.py` — the workflow generator itself (produces the Pegasus DAX/YAML).
  Never runs as a traced task under PMC — exclude from annotation.

## Execution architecture

Same PMC-only pattern as Montage — see [[dftracer-software-pegasus]] for the
full Condor(planning-only)/Pegasus-5.0.7/PMC-from-source install recipe. No
workflow-specific deviations found; the generic Pegasus skill applies as-is.

Default project docs point at HTCondor `condorpool`/Cori-Bosco/Decaf
execution modes — ignore these, use PMC per this project's standing
convention (no site-wide Condor pool on Tuolumne).

## Baseline run sizing

README reports single-chromosome, 10-individuals-jobs baseline at **~3.9
hours** (individuals: ~11,400s / 81.85%, frequency: 10.68%, rest ~5%). Far
too long for a bounded pipeline smoke/baseline run — scope down via
`prepare_input.sh` options and/or `daxgen.py -i <num_individuals_jobs>` to
target a ~10-20 minute run (fewer individuals-jobs and/or a reduced VCF row
count), consistent with this project's run-sizing convention for other
workloads.

## Annotation notes

- Pure Python FUNCTION-mode annotation via `python_annotate_project` /
  `python_annotate_file` works cleanly on all 5 target scripts.
- **Module-level (script-scope) I/O**: `mutation_overlap.py` and
  `frequency.py` each execute a top-level `tarfile.open()/extractall()/close()`
  block outside any function — no decorator tool can reach this. See
  `dftracer-annotation-lessons` PP9 for the manual `with DFTracerFn(...):`
  fixup pattern.
- `individuals.py` annotation should prioritize the VCF read loop and the
  per-individual file-write + tar-compress path — that's where the 81.85%
  of wall time lives.

## Resolved tool bug: session_annotation_report false 0/0

`session_annotation_report` returned `0/0 functions (0.0%)` for this app
despite real annotations under `annotated/bin/*.py`. Root cause: an
unmodified full clone was also left behind at `annotated/source/` (87 files,
identical content to `source/`); the tool's `_resolve_annotated_root` picked
the candidate root by raw file-existence overlap (87 > 5) instead of actual
edits, so it diffed identical trees and found nothing. Fixed in
`_count_counterparts` (`src/dftracer_agents/mcp_tools/tools/session/annotation.py`)
to score candidate roots by content-differs-from-source, not path overlap.
After the fix: 5/5 files, 38/38 functions, 100% coverage. If a future session
sees a similarly-wrong `annotated/` root pick, check for leftover unmodified
clones sitting alongside the real annotated output directory.

## Optimization findings (STEP 8/9, 2026-07-26): individuals.py I/O + compute fix

`individuals.py` (81.85% of baseline wall time) had a genuine algorithmic defect: an
`O(rows x individuals x ncols)` per-individual loop recomputing row-invariant VCF-parse work
for every one of 2504 individuals, plus a `compress()` step that wrote per-individual scratch
files then reopened/re-read every one of them into a tar archive (write-then-reopen round
trip). Two composed fixes, both behind env gates defaulting to OFF (baseline path bit-for-bit
unchanged, measured +0.3% i.e. noise, when gates are off):

- `IND_ROW_PRECOMPUTE=1` -- hoists all row-invariant VCF-parse work into a single
  `precompute_rows()` pass, plus a compact 1-byte/individual allele string. **-93.4%
  standalone** (90.65s -> 6.00s median, n=5, disjoint ranges).
- `IND_TAR_STREAM=1` -- in-memory `tarfile.addfile(TarInfo, BytesIO)` streaming, removing
  2504 scratch create/stat/read/unlink calls per task. Composed with the above:
  **-99.5% (193x) standalone** (90.65s -> 0.47s median, n=5).
- Also applied unconditionally: removal of `chrp_data`, a dead write-only accumulator
  (-25.8% peak RSS, wall-neutral, byte-identical output).
- `gc.disable()+gc.freeze()` was tried and REJECTED: **+1.7% regression** (acyclic object
  graph, see dftracer-memory-optimization).

**Correctness verification method (reproducible, use this pattern for any per-file-fan-out
optimization):** extract the output tar member-by-member for each configuration and compare
against the pristine baseline's extracted members via per-member md5 -- **2504/2504 members
identical** across gates-off, `IND_ROW_PRECOMPUTE=1` alone, and both gates combined. Also ran
`diff -rq` over all 2504 per-individual output files (no differences) and traced the code
line-by-line to confirm preserved edge-case semantics (e.g. the AF string vs. float
distinction, the `split(';')[8]` IndexError location, per-row `float()` ValueError skip
behavior) held identically before and after.

**Workflow-level (DAG, 4-node PMC) result:** 316.5s -> 244.7s, **1.29x (29.3%),
71.8s saved**, 46/46 tasks succeeded. This is well below the 193x standalone task-level
number BY DESIGN, not a partial win -- `individuals.py` is 82% of task work (hard ceiling),
and the DAG's own critical-path structure (level-4 `individuals_merge` is a hard width-1
serialization barrier; levels 5-7 including `frequency`/`mutation_overlap` were untouched)
re-dominates the makespan once `individuals` collapses. The optimizer's own pre-run
prediction ("workflow-level gain will be much smaller than the standalone number, capped by
the merge barrier and untouched downstream tasks") was confirmed by the measured 29.3%.
Always compute DAG critical-path structure (level widths from the `.dag` file) BEFORE
predicting a workflow-level speedup from a task-level fix -- see
dftracer-communication-optimization's DAG-max-width rule.

## Known upstream bug, reported not fixed (do NOT fix -- would change results)

`mutation_overlap.py`'s `pair_individuals` function was found (STEP 8 optimizer review) to
include self-intersections when building individual pairs -- i.e. an individual can be paired
against itself in the overlap computation, which is very likely an upstream logic bug in the
original 1000genome-workflow repo, not something introduced by this session's annotation or
optimization work. This was deliberately left UNFIXED: fixing it would change the numerical
output of `mutation_overlap.py` (a correctness-affecting change, not a performance
optimization), which is out of scope for a dftracer optimization pipeline and would break the
apples-to-apples baseline/optimized comparison. Reported here for visibility if a future
session works on this app's correctness rather than its performance.
