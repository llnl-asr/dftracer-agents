---
name: project-ray-molformer-4node-baseline-and-optimization-round2
description: ray_molformer 4-node/16-GPU baseline achieved (6 bugs fixed) and 4-dim optimization round 2 complete with an honest negative result plus 3 diagnostic corrections; straggler-skew fix documented as next step
metadata:
  type: project
---

**Session:** `ray_molformer/<session>` (Tuolumne, AMD MI300A, ROCm 6.2.1, IBM MoLFormer on
Ray Train/Tune). Continuation of an earlier 2-node baseline+optimization pass
([[project-ray-molformer-gcs-diagnostic]], [[software-ray-molformer]]).

**Headline this pass:** a fresh 4-node/16-GPU `baseline_4node` run succeeded end-to-end for
the first time, after root-causing and fixing 6 previously-undiagnosed bugs: (1) new
run_name's dataset dir not auto-symlinked to the parallel filesystem, (2) HF model cache must
be on PFS not `$HOME`, (3) app pins a specific HF revision a plain download misses, (4)
`transformers`' dynamic-module cache has a genuine concurrent first-write race across actors
(fixed with a single-process pre-warm), (5) the worker-join retry loop's success-string check
never matched what Ray actually prints, inflating apparent GCS flakiness, (6) `default_worker.py`'s
dftracer HIP-tracing init ran before `torch` was imported, crashing rocprofiler-sdk. Full-run
PyTorch Profiler coverage was also ported into the actually-run entry script. Result: 12
iterations, exit 0, 27.3M events / 56 chunks, all 9 categories confirmed.

**Optimization round 2 (4-dimension dispatch, Pipeline Policy #14):** honest NEGATIVE result.
Two compute/L2 variants (targeting the 58.9s `hipModuleLoad` GPU code-object relocation cost)
were applied and measured — both null (+1.1%, within noise, for the corrected-target variant).
No optimization is recommended for adoption; `baseline_4node` remains the best config found.
The value of the round was three diagnostic corrections, not a speedup:
1. Disproved "redundant HF model load" as the bring-up cost — real cost is `hipModuleLoad`
   (58.9s), immovable from user space.
2. Re-attributed 541.7s of RCCL collective time from `kernel_dispatch` (where HIP tracing
   hides it) back to communication — real comm cost is ~848s, not the 306.9s the `comm`
   category tag alone showed. Root mechanism: rank/straggler skew (87% of time in the tail),
   not bandwidth.
3. Confirmed memory has no optimization target — zero HF checkpoint weight files are ever
   read (model built from config).

**Untried next step (documented, not pursued this session):** balanced DDP sharding /
`drop_last=True` to reduce straggler skew at the allreduce barrier — the highest-value
remaining lever, ~10% app / 8% system potential per the merged proposal table.

**Known blocker this session:** six approved skill-update proposals (memory-optimization,
software-ray, system-tuolumne, communication-optimization, compute-optimization,
software-molformer) could NOT be persisted — the `Edit` tool refused to write outside its
assigned git worktree, and that worktree's local skill tree is missing/diverged from the
skills actually used by this session (loaded via the graph/session tools from the main
checkout). The next agent with unrestricted Edit access should apply the six proposed diffs
(full text captured in this session's `final_report/REPORT.md` §9/§11) and run `agents_sync`.

**Other confirmed tool bug:** `mcp__dftracer__comparator` failed 3x this session on a
2-node-vs-4-node cross-run comparison — see [[bug-dftracer-comparator-fails-ray-molformer]].

**final_report:** assembled, completeness/README checks both pass, PDF generated. NOT
self-contained-validated this session (a full 5-run flux reproduction was out of scope for a
report-only pass) — only a lightweight structural check (bash -n syntax, no-leaked-path grep)
was done. `validated=False` in the report's own metadata; flag for a future session to run the
real `scripts/run_all.sh <alloc>` reproduction before marking validated=True.

See also [[software-ray-molformer]] for the full technical bug-fix writeup and
[[project-ray-molformer-gcs-diagnostic]] for the earlier 2-node pass.
