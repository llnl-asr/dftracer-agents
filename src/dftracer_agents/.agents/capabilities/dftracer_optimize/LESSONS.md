# Lessons: dftracer_optimize

- [L-467e4b53] session_generate_optimization_proposals reported all 24 bottlenecks unsupported (posix_data/read/write/close/open/metadata_ops_slope) -> the strategy table covers absolute bandwidth/IOPS metrics, not DFDiagnoser's slope (accelerating-rate) metrics -> map slopes manually: data_ops_slope -> transfer size/ROMIO hints/striping; close_ops_slope -> stagger closes/ind_wr_buffer_size/client cache; metadata_ops_slope -> shared file/pre-create/DNE, cited from the iteration's Lustre papers (2026-06-22)

- [L-268691c0] comparator between opt{N-1}/traces_split and opt{N}/traces_split showed 0 delta and identical chunk md5s -> session_optimization_iteration copied all previous iteration traces into opt{N}/traces/ and split the combined set (194 old + 96 new) -> isolate the new-run traces into a clean dir, split(force=True) into a fresh output_dir, compare against the previous split (2026-06-22)

- [L-e3684dab] H5Pset_page_buffer_size had no effect and H5Fcreate_async fell back to sync; posix_close_ops_slope stayed -> HDF5 1.10.x has no page buffering with the MPI-IO VFD and no async VOL, so L2 changes compile and run with zero effect -> build parallel I/O apps against HDF5 >= 1.14 (--enable-parallel) and verify with h5cc -showconfig (2026-06-20)

- [L-408cb2ee] write bandwidth collapsed 352 -> 95 MiB/s and the job went 169 s -> >600 s with MPICH_MPIIO_HINTS=*:romio_ds_write=disable on VAST -> on VAST NVMe ROMIO data sieving aggregates non-contiguous HDF5 collective writes; disabling it issues thousands of small writes -> never disable romio_ds_write on VAST; test with hints unset first, then add romio_cb_write=enable only (2026-06-24)

- [L-f74b87f2] read bandwidth fell 2163 -> 659 MiB/s (-70%) with romio_cb_read=enable on VAST -> collective read buffering funnels 192 readers through few aggregators; VAST serves parallel reads natively (cb_read helps only on high per-request-latency storage like Lustre or disk) -> use romio_cb_write=enable only on VAST; test cb_read vs none explicitly elsewhere (2026-06-24)

- [L-eebe6db2] posix_seek_ops_slope (peak 362) and posix_data_ops_slope (74.3) stayed critical through L1 changes (-t 16m, -Y) on IOR HDF5/VAST -> 192 processes each wrote non-contiguous 512 KiB regions: 98,304 write+lseek pairs per iteration -> MPICH_MPIIO_HINTS=*:romio_cb_write=enable with -t 16m and -Y (synergistic): -33% time, +58% write BW, seek slope -97% (2026-06-24)

- [L-2073068c] a 'moved to Lustre' L3 result (+16% bandwidth) for a Pegasus/PMC workflow was invalid -> PMC executes in <CWD at plan time>/wf-scratch/..., not the site catalog sharedScratch; wf-scratch is shared by workflow name + run number across --dir plans -> cd into a Lustre directory before pegasus-plan, verify the TASK -w path in the generated dag starts with the Lustre mount, move the previous wf-scratch aside between measurements (2026-07-06)

- [L-ee915c7f] considered an L1 rewrite of Montage's row-by-row fits_read_pix/fits_write_pix into batched multi-row reads -> per-row I/O bounds memory for arbitrarily large mosaics by design and there were no pixel-correctness regression tests -> rejected the L1 change; applied the safe L2 posix_fadvise(SEQUENTIAL) hint on a separate fd instead (+16.1% bandwidth, ops/bytes unchanged) (2026-07-06)

- [L-9e4b7c38] 'POSIX_FADV_SEQUENTIAL undeclared' when adding posix_fadvise to a -std=c99 C file -> feature-test macros only apply before the first system header; fcntl.h's guard had already fired -> put #define _DEFAULT_SOURCE as the first line, before any #include, then #include <fcntl.h> (2026-07-06)

- [L-99843f8e] ROMIO hints bought only 18% on Flash-X -> one rank still wrote 91% of the bytes: the application serialized the bottleneck -> apply L1 before L2/L3; the 7.6x came only after the L1 rebuild removed the single-writer funnel (undated)

- [L-9c6b1013] NCCL 'remote process exited' crash during parallel component dispatch, twice in one session -> a sibling subagent edited a file in the shared annotated/source tree that the running multi-rank job was loading -> assign files per subagent, gate changes behind env vars defaulting to pre-change behavior and pin siblings' gates, or give each subagent its own source snapshot (undated)

- [L-00fb550d] a variant 'won' on wall clock -> it ran fewer epochs / fewer checkpoints / less data (doing less) -> fix epochs, problem scale, dataset size and checkpoint_interval across variants; check event and byte counts before crediting a speedup (undated)
