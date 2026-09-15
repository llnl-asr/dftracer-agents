---
name: software-cupti-pointer
description: CUPTI (NVIDIA GPU tracing backend for dftracer) knowledge lives in the software-cupti skill — load it, don't re-derive
metadata:
  type: reference
---

Everything learned about CUPTI — dftracer's NVIDIA GPU tracing backend — is
recorded in the `software-cupti` skill. Load that skill rather than
re-deriving any of it.

Highest-value facts it carries (pointers only, full detail in the skill):

- dftracer uses CUPTI's **Activity API** only → a GPU *timeline*, **not**
  hardware counters. `ncu`/PerfWorks is the counter tool; PAPI is CPU-side.
- CUPTI ships inside the CUDA toolkit, in two layouts (`extras/CUPTI` ≤10.1,
  merged `include/`+`lib64/` ≥10.2). A toolkit can exist with **no CUPTI in
  it**, and the build then silently disables CUDA tracing — verify from the
  `found CUPTI at …` configure line.
- Activity record structs are **versioned and deleted over time**, so the
  struct set must be chosen per toolkit version, keyed on the CUDA toolkit
  version — *not* `CUPTI_API_VERSION` (11.5 and 11.6 share API version 16 but
  need different kernel structs).
- CUPTI timestamps are ns on CUPTI's own clock and are rebased onto the
  dftracer timeline by a lazily-sampled offset; the correctness property is
  "CUDA events fall inside the app region that issued them".
- Two silent run-time gates: **one CUPTI client per process** (conflicts with
  nsys/ncu/torch.profiler) and `NVreg_RestrictProfilingToAdminUsers`, which is
  set at driver load and cannot be changed from inside a job.
- As of 2026-08-26 CUPTI and PAPI/variorum/service-telemetry lived on
  **separate unmerged branches** — check `CMakeLists.txt` for the option names
  before promising a build that needs both.

Related: [[software-rocm]] is the AMD counterpart; [[software-papi]] covers the
CPU-counter side; [[bug-dftracer-torch-profiler-rocprofiler-conflict]] is the
ROCm analogue of the one-profiler-per-process rule.
