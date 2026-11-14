# What worked: dftracer_optimize_communication

One entry per key, newest wins. Portable: no user paths.

- [tool:opt_communication_checklist] 2026-11-13: render the 9 dimensions and the comm_wait strategies, fill one row per dimension (applied / applicable + reason / not_applicable + reason, level, citation, app_impact_pct, system_impact_pct), then dftracer_optimize_communication_check until status pass.
- [ray_molformer/ddp/comm-attribution] 2026-08-02: with DFTRACER_ENABLE_HIP_TRACING=ON, select KERNEL_DISPATCH events whose name contains ncclDevKern, move their time from compute to communication, then rank by kernel-duration percentiles (bimodal -> straggler skew -> balance per-rank work; cite arXiv:2505.23523).
- [1000genome/pegasus-pmc/comm-ground-truth] 2026-07-26: take the PMC [cluster-summary ... bytes_sent=<N>] line as the communication ground truth for an unannotated scheduler; compute DAG level widths from the .dag file before any worker-count lever.
- [pecan/ddp/comm-check] 2026-07-20: count cpu-gpu-transfer events through the trace tools to size communication (25.58 s, <1% of wall) and leave it unoptimized when I/O dominates by two orders of magnitude.
