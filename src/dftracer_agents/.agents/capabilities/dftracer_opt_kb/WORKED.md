# What worked: dftracer_opt_kb

One entry per key, newest wins. Portable: no user paths.

- [tool:opt_kb_lookup] 2026-11-13: call with system, workload and the detected software list (comma-separated) before proposing; read rows' notes and carry verdict/delta into the proposal table's prior column.
- [tool:opt_kb_record] 2026-11-13: validate the entry with dftracer_opt_kb_check first; record medians of >= 5 replicates with the band in notes; metric_scope=system with the paired app metric; then opt_kb_render.
- [tool:opt_proposal_table] 2026-11-13: pass every checklist candidate with level, bottleneck, change, citation, app_impact_pct, system_impact_pct, status and skip_reason; apply rows in table order one at a time.
- [h5bench/tuolumne/io-L3] 2026-07-08: MPICH_MPIIO_HINTS="*:romio_cb_write=enable:cb_nodes=16" + CRAY_CB_NODES_MULTIPLIER=2 + lfs setstripe -c 16 -S 4M on a fresh output dir: 325.94 -> 1924.71 MB/s at 768 ranks; FI_CXI_RDZV_THRESHOLD=16384 on top: 3238.05 MB/s.
- [megatron_deepspeed_bert/tuolumne/comm-L3] undated (KB session megatron_deepspeed_bert): site aws-ofi-rccl plugin dir on LD_LIBRARY_PATH (no NCCL_NET) so RCCL uses AWS Libfabric instead of TCP sockets: 177.92 -> 100.78 s for 400 iterations.
