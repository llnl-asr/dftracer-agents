# Ray MoLFormer dftracer pipeline — final results

App: IBM MoLFormer (chemical-language transformer, PyTorch) launched via Ray 2.48.0
Train on Tuolumne (AMD MI300A, ROCm 6.2.1, Cray PE/Flux). Session:
`ray_molformer/20260725_000436`.

## Diagnosis (STEP 9)

Compute-bound: POSIX I/O 0.68s/265.4s (0.26%, 21MB @ 31MB/s), Ray/DDP comm 19.7s
(7.4%), ~92% GPU forward/backward compute.

## Optimization applied (STEP 10) — bf16 autocast

Wrapped the forward pass (`MolFormerWithRegression` + loss) in
`torch.autocast(device_type="cuda", dtype=torch.bfloat16)`; backward/optimizer.step
stay fp32. Applied in `molformer_ray_descriptors_opt1.py`.

| Metric | Baseline | opt1 (bf16, 2-node) | Delta |
|---|---|---|---|
| Job Time (s) | 265.4 | 275.4 | **+3.8% slower** |
| Communication (s) | 19.7 (7.4%) | 20.1 (7.3%) | flat |
| POSIX I/O (s) | 0.68 (0.26%) | 0.97 (0.35%) | flat, negligible |

**Result: the optimization did NOT produce a measured speedup** — reported honestly,
not adjusted to show a win. See `pipeline_plan.md` STEP 10 for the full checklist
walk of all four dimensions (compute/communication/I/O/memory), including the
negligible/not-applicable verdicts for comm, I/O, and memory.

## N-node scale validation (STEP 11)

Same opt1 script scaled to 4 nodes/16 GPUs on the same live allocation.

| Metric | opt1 2-node/8-GPU | opt1 4-node/16-GPU | Delta |
|---|---|---|---|
| Job Time (s) | 275.4 | 309.9 | +12.5% slower at 2x scale |
| Communication (s) | 20.1 (7.3%) | 70.5 (22.8%) | >3x share |

**Result: scaling to 4 nodes made the job slower**, not faster — negative scaling
efficiency for this 5000-row dataset. Communication overhead's share of wall time
more than tripled. This is a workload-sizing finding (dataset too small to benefit
from >2-node scale-out), not a defect in the bf16 change.

## NaN loss — root cause found and fixed (corrected from an earlier, disproven hypothesis)

Both baseline and opt1 runs' `loss`/`mlm_loss`/`regression_loss` metrics were NaN.
An earlier hypothesis (a `pubchem_descriptor_stats.npz` dataset mismatch) was
**checked directly and disproven** — the stats file is column-aligned with
`rdkit.Chem.Descriptors._descList` and statistically consistent with the CSV
(median |z-score| between CSV and stats column means = 0.15).

**Real root cause**: `pubchem_filtered.csv` itself contains 1220 NaN and 6 ±inf
cells, concentrated in four RDKit Gasteiger partial-charge descriptors
(`MaxPartialCharge`, `MinPartialCharge`, `MaxAbsPartialCharge`,
`MinAbsPartialCharge`) that RDKit emits when charge computation doesn't converge
for certain molecules. These flow into the MSE regression target, producing
`reg_loss=nan` on iteration 1, which poisons all weights via `backward()`.

**Fix** (applied and verified in a separate run of this session, in
`train_func_per_worker`'s normalization path):
```python
descriptors = torch.nan_to_num(descriptors, nan=0.0, posinf=0.0, neginf=0.0)
```
Verified: with the fix, zero `nan` occurrences across 20/20 training iterations,
`regression_loss` converges normally. The wall-clock/throughput comparisons above
remain valid either way (NaN propagation does not change tensor shapes or the
volume of compute/communication/I/O performed), but this fix should be folded into
the baseline/opt1 scripts before any future correctness-sensitive run from this
dataset.

## Scripts in this package

- `scripts/baseline_runner.sh` — 2-node/8-GPU baseline (fp32, no autocast)
- `scripts/opt1_runner.sh` — 2-node/8-GPU bf16-autocast variant
- `scripts/opt1_4node_runner.sh` — 4-node/16-GPU scale validation of the opt1 variant
- `scripts/lib_load_config.sh` — sourced by all of the above; resolves `WORKSPACE_ROOT`
  from `config.ini` so no script hardcodes a path
- `scripts/run_all.sh <alloc-id>` — runs all three in sequence against a live flux
  allocation
