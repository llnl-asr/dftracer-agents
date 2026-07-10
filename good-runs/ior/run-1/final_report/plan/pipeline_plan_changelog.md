
## 2026-07-10 STEP 7 dftracer-optimizer (IOR HDF5 Lustre)
- Ran comprehensive L1/L2/L3 sweep on alloc <flux-jobid> (8 nodes, 512 ranks, 256GB fixed).
- L1 WIN: -t 4k -> 4m => write +190% (21258->61788 MiB/s), read +91% (12022->22330). -t 1m weaker (50.4/21.1 GiB/s).
- L2 ROMIO cb_write/read=enable + CRAY_CB_NODES_MULTIPLIER=2: NEUTRAL (within noise); file-per-process defeats collective buffering.
- L3 lfs setstripe -c4 -S4m: REGRESSED write -19%; default /p/lustre5 PFL better for file-per-process. Combined (F) within noise band.
- BEST CONFIG: -a HDF5 -b 16m -t 4m -s 32 -C -F (L1 only). ROMIO hints optional/harmless.
- No-dftracer 4k baseline ~50s wall; 4k WITH dftracer ~10min (tracing 40.7M ops). Runs no-dftracer for pure BW.
- IOR self-cleans output (no -k) -> no manual Lustre deletion needed.

## 2026-07-10 (opt2) — dftracer-optimizer: CORRECTED loop at fixed -t 4k
- Correction applied: -t 4k->4m relabeled as DIAGNOSTIC characterization (transfer-size change = forbidden pattern swap), NOT "best config". Redid the loop holding the workload's REAL request size -t 4k fixed.
- Sweep script: scripts/sweep_4k.sh (512 ranks/8 nodes, -a HDF5 -b 16m -s 32 -C -F, alloc <flux-jobid>, /p/lustre5). Log: artifacts/07_sweep_4k.log.
- Results vs 4k baseline (write/read med MiB/s = 21737/12548, CV~2%):
  - A base4k: 21737/12548 (5 reps)
  - B data-sieving (romio_ds_write/read + ind buffers): -1.6%/-1.6% NEUTRAL (contiguous access => sieving doesn't engage)
  - C collective buffering (cb_write/read + CBMULT=2): -1.5%/-6.4% inert under -F (COMM_SELF, nothing to aggregate); read drag
  - D Lustre stripe c4 S4m: +0.3%/-0.4% NEUTRAL — the -19% write regression seen at 4m did NOT reproduce at 4k (transfer-size-dependent)
  - Lustre readahead: admin-only (Permission denied), already 512MB default — not tunable
- CONCLUSION: NONE of the coalescing/buffering/caching/striping techniques beat the -t 4k baseline. Best config that preserves the real pattern = plain -t 4k (no technique). Honest no-win finding recorded to KB (4 entries) + rendered.
