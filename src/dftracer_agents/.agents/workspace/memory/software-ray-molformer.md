---
name: software-ray-molformer
description: ray_molformer pipeline — FULL PIPELINE COMPLETE (STEPs 1-13). Baseline traced, analyzed as compute-bound, bf16 optimization measured as no-win, 4-node scaling measured as a regression, NaN bug root-caused and fixed, final_report validated, privacy scan clean.
metadata:
  type: project
---

**Canonical home:** see the `software-ray` skill (jemalloc/`ray start --head` crash,
multi-node bring-up) and `software-molformer` (app-level build/annotation caveats) —
the Ray+MoLFormer-specific compute/NaN findings in this file have no separate skill
and remain the fullest record of that combination.

Continuation of [[software-ray-molformer]] — final pipeline outcome.

## Pipeline complete: STEPs 1-13 done

- STEPs 1-9: see prior entries (annotation/build/smoke/baseline-trace/analyze/diagnose — all complete, compute-bound diagnosis: I/O 0.26%, comm 7.4%, ~92% GPU compute).
- STEP 10 (optimizer): bf16 autocast applied to the forward pass (backward/optimizer.step stay fp32) — **measured result: +3.8% SLOWER** (265.4s baseline vs 275.4s bf16, 2-node/8-GPU), reported honestly as a negative finding, not adjusted. Communication and I/O dimensions confirmed negligible/not-applicable with concrete evidence (comm 7.3-7.4% flat, I/O 0.26-0.35% flat); memory dimension confirmed not a bottleneck (22.5GiB/490GiB peak, zero object-store spill). See the compute-dimension sub-analysis elsewhere in this memory file for the deeper finding that Ray Train worker STARTUP is ~74% of wall time (not the 20-iteration training loop, ~25%) — this is why a compute-layer change like bf16 has almost no headroom to show a win regardless of its own effectiveness.
- STEP 11 (N-node validation): opt1 (bf16) scaled to 4 nodes/16 GPUs on the same live allocation — **measured result: scaling made it slower**, 309.9s vs 275.4s at 2-node (+12.5%), with communication's wall-time share more than tripling (7.3% -> 22.8%). This is a workload-sizing effect (5000-row dataset too small to benefit from >2-node scale-out for this app), not a defect in the optimization itself.
- STEP 12 (final_report): assembled at `<WS>/final_report/`, self-contained (`config.ini` is the only real-path location, `scripts/lib_load_config.sh` sourced by every script). Independently re-validated by re-running `opt1_runner.sh` from an isolated `final_folder_validate/` package against the live allocation — reproduced 20/20 iterations, 262s wall time, within noise. `session_final_report(validated=True)` called.
- STEP 13 (privacy guard): `privacy_scan()` on default paths (skills/agents/workspace) was clean on first pass. **`final_report/` required an explicit path argument** — it is NOT in the tool's default scan set, a real gap (matches the previously-documented `bug-privacy-scan-final-report-gitignore-blindspot` pattern, worth re-checking that carve-out actually covers this session's final_report layout). Found and redacted 11 files with real paths/job-ids/username inside `final_report/`; re-scan confirmed clean.

## NaN loss bug: root cause found and FIXED (see full detail in the compute-dimension entry earlier in this file)
Real cause: `pubchem_filtered.csv` has 1220 NaN + 6 inf cells in RDKit Gasteiger partial-charge descriptor columns (RDKit non-convergence), not a stats.npz/dataset mismatch (that hypothesis was checked and disproven). Fix: `torch.nan_to_num(descriptors, nan=0.0, posinf=0.0, neginf=0.0)` after normalization in `train_func_per_worker`. Verified zero NaN across 20/20 iterations with the fix. `final_report/RESULTS.md` was corrected to reflect this (an earlier report draft cited the disproven stats.npz hypothesis before this fix was found — always cross-check a final_report's stated caveats against the LATEST diagnostic findings before treating a report as final).

## Process anomaly worth remembering: concurrent duplicate agent lineages
This session dispatched two independent agent lineages for STEP 10-13 in parallel without realizing it (an orchestrator that fanned out 4 dimension-specific subagents, AND a separately-dispatched "direct executor" told to do the full STEP 10-13 loop itself) — both wrote to the same session workspace concurrently, launched independent flux jobs (`opt1`/`opt2`/`opt2r2`/`opt3`/`baserep`/`validate` all appeared in `flux jobs -a` simultaneously, some for 30-40 minutes), and both reached partially-overlapping conclusions via different exact numbers (e.g. bf16 "no_change 0.0%" from one lineage's interleaved-control measurement vs "+3.8% slower" from the other's) — directionally consistent (bf16 doesn't help) but not numerically identical, itself another confirmation of the interleaved-control-timing-noise lesson. No node/GPU collision occurred, but it was close (flagged explicitly by one of the compute subagents). **Lesson: when dispatching a background agent for a multi-step pipeline phase, only ever have ONE live agent authorized to launch jobs/edit the shared annotated source tree for that phase at a time** — if a first dispatch's status is unclear (e.g. it returns early claiming to have delegated further without verifiable evidence), positively confirm it is fully stopped (check `flux jobs -a` for its jobs) before dispatching a second attempt at the same phase, rather than assuming the first is dead.

Session workspace: `$PROJECT_ROOT/workspaces/ray_molformer/20260725_000436` (session-local). This is the final entry for this pipeline session.
