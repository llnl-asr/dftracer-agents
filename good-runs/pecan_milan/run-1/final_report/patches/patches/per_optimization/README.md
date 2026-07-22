# Per-optimization patches

`../annotated.patch` is the single authoritative, `git apply`-able diff covering
EVERYTHING (dftracer annotation + every optimization in this report, applied
cumulatively in place against `annotated/source/`).

The files in this directory break that single diff into one excerpt per
optimization from the Optimization Ledger (see `../../REPORT.md` §7), for
readability. **They are documentation excerpts, not independently
`git apply`-able patches** — the optimizations were applied directly to
`annotated/source/` one after another without a per-stage git commit or
source snapshot at each step, so there is no clean historical baseline to diff
each one against in isolation. Each file's before/after code is reproduced
verbatim from the real diff; only the framing (which lines belong to which
named optimization) is manually curated.

| File | Ledger row | Dimension | Verdict |
|---|---|---|---|
| `opt1_decode_cache.diff` | #1 | I/O | Kept — the real win (-8.5%) |
| `opt2_attr_hoist.diff` | #2 | I/O | Kept, no independent win |
| `opt3_prefetch_factor.diff` | #3 | I/O | Kept |
| `opt4_torch_compile.diff` | #4 | Compute | **Reverted** — see REPORT.md §7 mechanism note |
| `opt5_loss_sync_rewrite.diff` | #5 | Compute | Kept — mechanism-confirmed real win |
| `opt6_memory_alloc_conf.diff` | #6 | Memory | Kept, no independent win |
| `opt9_worker_finalize_fix.diff` | #9 | Tooling (trace-data integrity, not perf) | Kept — data-integrity fix |

For the full cumulative diff (equivalent to applying all of the above plus the
original dftracer annotation work), use `../annotated.patch`.
