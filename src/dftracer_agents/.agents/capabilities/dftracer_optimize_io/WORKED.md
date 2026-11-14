# What worked: dftracer_optimize_io

What finally worked, portable (${WORKSPACE}, ${LUSTRE}, <dataset_dir>). One entry per key, newest wins.

- [scaffold/train/dataset-placement] 2026-07-10: dataset copied from an NFS workspace to `${LUSTRE}/<user>/...` with matched striping and the app's data-dir argument repointed; removed the posix_*_ops_slope ceiling with no app code change (verify `stat -f <dataset_dir>` says lustre).
- [ml-dataset/any/striping] 2026-07-10: `lfs setstripe -c 1 <dataset_dir>` before copying many small per-sample files in; `lfs getstripe -c <dataset_dir>` reports 1 and each sample sits on one OST.
- [checkpoint/any/striping] 2026-07-10: few large shared files or sequential checkpoints: `lfs setstripe -c 4..8 -S 4m <dir>` before the first file is created (4 MB matches ROMIO cb_buffer_size 64 MB / 16 aggregators).
- [vpic-kokkos/benchmark/io-sizing] 2026-07-14: before any striping/ROMIO change, compare ops vs bytes vs I/O seconds (340.85M ops, 325 MB, 1.65 s of 210.2 s): negligible op-count-bound I/O, no I/O lever needed.
- [ior/hdf5/ab-method] 2026-07-24: headline numbers from untraced runs with identical DFTRACER_ENABLE/DFTRACER_INIT across arms, a fresh output directory per replicate, arms interleaved, >= 5 replicates, claim only non-overlapping ranges.
- [1000genome/individuals/tar-stream] 2026-07-26: `IND_TAR_STREAM=1` (stream members into the open TarFile with `tarfile.addfile(TarInfo, BytesIO)` instead of `tarfile.add(dir)` after the producer loop): -13.2% standalone.
- [flash-x/io/ordering] L1 before L2: removing the single-writer funnel with the L1 rebuild gave 7.6x where ROMIO hints alone gave 18%.
