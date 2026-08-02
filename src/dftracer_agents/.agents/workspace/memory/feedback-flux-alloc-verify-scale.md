---
name: feedback-flux-alloc-verify-scale
description: Verify optimization-variant run scale and completion before crediting any wall-time delta — a half-scale cancelled run produced a misleading comparator number
metadata:
  type: feedback
---

**Canonical home:** see the `flux-alloc` skill ("Verify scale and completion before
crediting ANY wall-time delta" section already carries the full writeup).

Before comparing an optimization variant's wall-time against baseline, verify the variant actually ran at the SAME scale (rank/process count) and COMPLETED (no `job.exception cancel` in its flux event log).

**Why:** during a PECAN optimization pass, an I/O-fix variant (`opt1`) was accidentally launched at half the baseline's scale (2 nodes/8 ranks via a leftover script instead of the intended 4 nodes/16 ranks) AND got cancelled mid-flight when the shared flux allocation died. The comparator tool still dutifully produced a number (31.4s/38proc baseline vs 7.0s/16proc opt1) that looked like a measurement but was confounded by both scale and truncation simultaneously.

**How to apply:** check the comparator's own process-count field against the expected baseline count, and check `flux job info <jobid> eventlog` for `job.exception cancel` (or any non-zero exit) before trusting any wall-time delta. If either check fails, fall back to a scale/truncation-robust work-normalized metric (ops-per-sample, opens-per-`__getitem__`, bytes-per-record) instead, and report the wall-time speedup as NOT YET MEASURED rather than laundering a confounded number into a clean-looking delta. See [[flux-alloc]] skill for the full writeup.
