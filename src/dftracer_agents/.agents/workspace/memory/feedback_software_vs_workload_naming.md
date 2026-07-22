---
name: feedback-software-vs-workload-naming
description: "Skill-naming convention correction — apps being annotated/instrumented by dftracer use software-<name>, not workload-<name>"
metadata: 
  node_type: memory
  type: feedback
  
---

`workload-<app>` is reserved for scientific/HPC workloads dftracer traces to
study I/O behavior (IOR, h5bench, Flash-X, VPIC-Kokkos, ScaFFold). A tool or
service that is itself being annotated/instrumented by dftracer as software
under test — not a scientific workload — should use `software-<name>`
instead (e.g. `software-flux-fiction` for the Flux scheduling emulator).

**Why:** User corrected an auto-created `workload-flux-fiction` skill,
stating flux-fiction is software annotated by dftracer, not a workload.

**How to apply:** Before creating a new per-app skill during a build/annotate
session, judge whether the target is a traced scientific workload
(`workload-*`) or a tool/service being instrumented (`software-*`), and ask
if genuinely ambiguous rather than defaulting to `workload-*`.
