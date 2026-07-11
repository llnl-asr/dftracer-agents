---
name: workload-montage
description: >
  Montage (Pegasus astronomy mosaic workflow) I/O bottleneck patterns —
  bottleneck ranking is scale-dependent between pilot and full-DAG runs.
  Load this skill whenever working with Montage or Pegasus mosaic traces.
---

Cross-references: [[dftracer-trace-utils]] [[software-pegasus]] [[dftracer-diagnoser]]

## Bottleneck ranking flips between pilot scale and full-DAG scale

Montage/Pegasus splits a mosaic into many independent per-stage jobs
(mProjectPP/mDiffFit/mBackground/mAdd, etc.), each a short-lived OS process
with its own FITS header opens/stats/closes — not MPI ranks of one program.

**Do not generalize a small pilot run's bottleneck ranking to production
scale.** Observed on a real session (9-job pilot vs a 429-process 2MASS run,
same workflow):

| Rank at 9-job pilot (3.7s) | Rank at 429-process 2MASS run (52s) |
|---|---|
| `fopen64`/`fclose` overhead (~86ms, dominant) | still growing but no longer dominant |
| — | `__xstat64` (stat) cost exploded: +8670% mean dur, LARGE effect size — **new dominant bottleneck**, invisible at pilot scale |
| — | STDIO churn (1.5M+ events) becomes >97% of all POSIX+STDIO activity |
| `remove` cleanup, minor | `remove`/`unlink` scaled non-linearly (10x more calls, 27x higher per-call latency) |
| sub-KB avg POSIX transfer size | same pattern persists, but aggregate bandwidth *improves* 94% at scale (page-cache reuse across the wider job graph offsets the small-transfer penalty) |

**Implication:** always re-verify pilot-scale findings against a full/larger
run before recommending optimizations — `stat()`/metadata-server pressure and
STDIO-call-volume effects only become visible once process fan-out reaches
production DAG width. Per-process I/O-volume skew across pipeline stages is
expected multi-stage-workflow behavior, not a straggler bug — don't try to
"fix" it as if it were an MPI load-imbalance issue.

## Likely root causes worth targeting

- **`__xstat64` explosion**: likely Pegasus job-wrapper staging/existence
  checks re-`stat`-ing the same FITS files redundantly per stage, or
  metadata-server contention on a shared parallel FS at high process fan-out
  — not Montage application code itself.
- **FITS header parsing** (`montage_parseHdr`/`montage_checkHdr`) is
  text-based (ASCII key=value line parsing), so its per-call cost is
  structural to Montage, not an artifact of a particular run.
- **`fopen64`/`fclose` cost is proportional to file count** — each FITS image
  is opened/processed/closed independently per Pegasus job with no persistent
  handles across stages; not amortizable without changing the per-task file
  model.
