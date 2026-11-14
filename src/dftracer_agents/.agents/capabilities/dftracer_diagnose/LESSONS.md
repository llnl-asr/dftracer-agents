# Lessons: dftracer_diagnose

- [L-e052c511] diagnose() returned 0 metric observations across 0 view(s) on a 128-rank VPIC checkpoint whose _flat_view_*.parquet existed and whose analyze summary showed 340.85M POSIX ops -> posix/dlio presets produce empty views for non-matching cat values, and _diagnose_via_api checked hasattr(diagnose_checkpoint) on an old dfdiagnoser and fell to a pandas fallback that swallowed exceptions -> analyze with the generic preset; upgrade dfdiagnoser to a release with Diagnoser.diagnose_checkpoint (the API path now returns real DiagnosisFinding objects) (2026-07-20)

- [L-6ac1b3e7] diagnose still returned 0 observations after the code fix while the same function called directly in the venv returned 15 findings -> a stale MCP server process started before the pip --force-reinstall kept old modules in memory; 'restarts' started a new instance on another port while the old one kept answering -> ps -o pid,lstart,cmd -p <pid> vs the last install; kill that exact PID, start fresh, curl the new port; look for several stale instances (2026-07-20)

- [L-68bda06e] first diagnose_checkpoint() in the server crashed with 'ValueError: signal only works in main thread of the main interpreter' -> fact_engine -> analyzer.cluster -> dask_jobqueue runner.py calls signal.signal at import time and FastMCP tools run on a worker thread -> import dftracer.analyzer.fact_engine at module load in dfdiagnoser_service.py (fixed); later calls in the same process were unaffected (2026-07-20)

- [L-741de915] diagnose() scored posix_close_count_sum=1.0 as critical on VPIC -> percentile-based severity on a metric with fewer than 5 operations -> check the absolute count/magnitude behind every severity before treating it as a bottleneck (2026-07-14)

- [L-1ebacb07] analyze()/diagnose() reported 527,206 events / 4 processes vs 2,111,806 / 16 ranks on disk -> silent under-count of the trace -> cross-check against event_count or the rank file listing; on disagreement aggregate dur by name/cat with aggregator/view and label it manual analysis (2026-07-14)

- [L-7f0e1fd6] VPIC dump_energies annotated comp="io" diagnosed as an I/O cost -> 99.7% of its wall time was inside MPI_Allreduce; the label came from the name -> containment analysis of child spans before an I/O-side recommendation (2026-07-14)

- [L-d290a4c3] an apparently fast app with a tiny event count -> broken tracing, not a fast app -> verify trace size/event_count first and fix collection before diagnosis (undated)

- [L-d7efabad] comparator deltas misleading between two runs -> the runs had different chunk shapes/parallelism and comparator matches by chunk index -> flag differing chunk counts; compare isolated, equally split trace sets (undated)
