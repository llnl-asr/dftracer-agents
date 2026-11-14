# What worked: dftracer_analyze

What finally worked in analysis runs (portable: ${WORKSPACE}, ${SESSION}, <compact>, <run_name>). One entry per key, newest wins.

- [tool:analyze] 2026-07-20: `index(directory=<compact>, force=True, executor_threads=1)` first, then `analyze(analyzer_preset="generic")` with `analyzer.checkpoint=True` and a checkpoint_dir, on the session's flux allocation (`allocation_id=<jobid>`); posix/dlio only afterwards. Recovered every category on custom dft_event_logging apps (PECAN/PDBspheres) where posix/dlio returned empty views.
- [tool:analyze/hydra] 2026-06-24: dfanalyzer takes Hydra positional overrides: `trace_path=<p> view_types=[file_name,proc_name,time_range] analyzer/preset=<preset> analyzer.checkpoint=True analyzer.checkpoint_dir=<p> output=console cluster=local`.
- [tool:view/overlap] 2026-07-22: cross-process compute vs data-loading overlap on PECAN baseline5: `view(directory=<compact>, query='cat == "compute" OR cat == "communication-io"', output_file=<scratch>/events.ndjson)`, p99 bucket width with event splitting (0.824 s, 169 buckets), max busy per pid per bucket, pigeonhole bound: overlap_pct_of_compute 71.8%, compute stall ~28.2%.
- [tool:session_analyze_traces] 2026-07-10: folder-segregated compact layouts analyze with `subpath="<workload>/<rep>"` (e.g. "write/rep1").
- [tool:session_analyze_traces/reindex] 2026-06-22: after re-splitting, `rm -rf ${SESSION}/traces_split/idx/`, `split(force=True)`, then analyze again to get the new event count.
