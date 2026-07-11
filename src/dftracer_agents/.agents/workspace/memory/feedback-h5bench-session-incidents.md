---
name: feedback-h5bench-session-incidents
description: Runaway job-submission-loop incident, mpifileutils/drm cleanup pattern, and comparator-corroboration methodology from an h5bench session
metadata:
  type: feedback
---

## What happened

**Runaway job-submission loop:** mid-session, ~3,200 queued `h5bench_write` jobs were found
stacked across 5 flux allocations, each requesting a full 8-node/768-rank allocation (so only
one could ever run at a time per allocation) — the result of some process submitting in a bare
retry loop without waiting for the previous job to complete or checking occupancy first. Not
caused by the agent that found it; found already in progress while about to launch its own
calibration job, reported immediately rather than guessing/working around it, and resolved by
scoped per-allocation `flux cancel <jobid>` calls (never a global/bare `flux cancel --all`,
which is separately blocked by policy as too broad for a shared multi-tenant system).

**mpifileutils/drm cleanup pattern:** Lustre app output (hundreds of GB per run) must be
cleaned via `drm` (mpifileutils), never bare `rm -rf`, on a real HPC filesystem. Practical
notes: (1) `module load mpifileutils/0.12` in a bash tool session only affects that shell's
PATH — a `flux proxy <alloc> flux run ...` subprocess does NOT inherit it, so resolve the full
binary path once (e.g. via `module load` + `which drm`) and pass that absolute path to every
`flux run -n8 <path> <dir>` cleanup call. (2) `drm` on very large file counts (500k+ loose
files) can take several minutes — run it and wait, don't assume a quick client-side timeout
means it failed; check the actual file count afterward.

## Why

Both are recurring risks in any HPC-benchmarking pipeline: (a) job-submission loops without an
occupancy check can silently exhaust a shared scheduler's job history and starve other
allocations/users; (b) raw `rm -rf` on a striped parallel filesystem is slow and can leave
orphaned OST objects — `drm` is purpose-built for this and dramatically faster at scale.

## How to apply

- **Before ANY job submission into an allocation**, check current occupancy
  (`flux jobs -a | grep -cE ' R | PD | S '` on that specific allocation) and confirm it's 0 (or
  that the specific job you expect to free it has actually completed) before submitting the
  next one. Never submit in a bare loop without a completion check between iterations. (This is
  now a mandatory rule in the `flux-alloc` skill.)
- **Never use `flux cancel --all`** even when cleaning up a confirmed runaway loop — cancel by
  specific job ID, one call per stuck job, even if that means many individual calls. Bulk/global
  cancellation on a shared cluster risks destroying other users' or unrelated workloads.
- **Always clean Lustre app output via `drm`** (resolve its absolute path once per session,
  since `module load` doesn't propagate into `flux proxy` subprocesses), immediately after a
  trace is confirmed captured — never leave large `.h5` outputs sitting on a shared filesystem
  with a tight (20TB-class) quota budget.
- **Comparator corroboration required before any optimization "win" claim**: a 5-rep median/CV
  comparison alone is not sufficient on a noisy shared filesystem — always also run a same-rep
  `comparator` cross-check (baseline repN vs variant repN) and require it to show a
  non-negligible, ideally statistically-significant delta before trusting a median-level
  improvement. Three separate levers in this session showed large (+38-68%) median deltas that
  were pure noise once the same-rep comparator showed ≤3%, negligible. (This generic lesson now
  also lives in `dftracer-optimization-kb` Rule 5.)

See [[project-h5bench-read-write-optimization]] for the substantive optimization results this
session produced.
