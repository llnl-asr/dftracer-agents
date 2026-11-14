# What worked: dftracer_optimize_compute

What finally worked, portable. One entry per key, newest wins.

- [vpic-kokkos/openmp/threads] 2026-07-14: `flux run -c<cores_per_task> ...` with `OMP_NUM_THREADS=<cores_per_task> OMP_PROC_BIND=spread OMP_PLACES=cores` (6 on Tuolumne at 16 ranks/node): 210.2 s -> 124.0 s at 128 ranks / 8 nodes, runtime env only.
- [pecan/ddp/loss-sync] 2026-07-22: in train_one_epoch/eval_one_epoch build targets with `torch.stack([x.to(device).view(()) for x in batch])`, drop the `.cpu().float()` calls on the loss terms, keep one `.item()` for the logged loss: epoch-2 -3.5%/-9.0% (single replicate), bit-identical loss on fixed-seed batches, 48/48 non-empty traces.
- [pecan/ddp/compile-deconfound] 2026-07-22: compare a 3-epoch uncompiled baseline with the 3-epoch compiled run (epoch-3 vs epoch-3) to remove the page-cache warmup confound (result: no torch.compile gain).
- [1000genome/individuals/hoist] 2026-07-26: single-pass `precompute_rows()` hoisting loop-invariant parsing out of the per-individual loop: -93.4% standalone.
- [rajaperf/hip/sizing] 2026-08-05: size launch/sync-bound kernels with the app's own untraced timer; 5 interleaved replicates per arm before crediting a spin/interrupt knob.
