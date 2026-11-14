# What worked: dftracer_diagnose

What finally worked (portable placeholders). One entry per key, newest wins.

- [tool:session_diagnose_bottlenecks] 2026-07-20: after split/compact, `session_diagnose_bottlenecks(run_id, analyzer_preset="generic")` (dfanalyzer with checkpoint, then DFDiagnoser `diagnose_checkpoint`) writes `${SESSION}/diagnosis.json` with severity_counts and the ranked score >= 4 bottlenecks; for an optimization iteration pass `traces_dir=${SESSION}/opt<N>/traces/compact`.
- [tool:diagnose/stale-server] 2026-07-20: when diagnose returns 0 observations but the code is fixed: `ps -o pid,lstart,cmd -p <pid>` on every server process, kill the one older than the last pip install, start fresh, verify the port with curl, re-run: real findings (15).
