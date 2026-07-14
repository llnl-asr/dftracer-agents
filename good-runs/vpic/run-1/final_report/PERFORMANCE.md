# Pipeline performance report — vpic_kokkos/20260714_155730

## Summary

- **Cost:** $0.0000 across 0 API calls
- **Tokens:** 0 total — 0 in, 0 out, 0 cache-read, 0 cache-write
- **Time:** 9965.9 s wall, 701.5 s inside steps
- **Steps:** 7 (3 succeeded, 0 failed, 0 running)
- **Attempts:** 7 tries, 0 retries, 0 failed
- **Tools:** 0 calls (0 MCP), 0 failed, 0.0 s total
- **API errors:** 0 · **Compactions:** 0
- **MLflow:** http://127.0.0.1:10002/#/experiments/1/runs/&lt;mlflow-run-id&gt;

## Per-step

| # | Step | Agent | Status | Tries | Retries | Failed | Exec (s) | Wall (s) | Cost (USD) | Tokens | API | Tools |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | STEP 6: dftracer-tracer | dftracer-tracer | ok | 1 | 0 | 0 | 241.8 | 241.8 | 0.0000 | 0 | 0 | 0 |
| 2 | STEP 7: dftracer-analyzer then dftracer-diagnoser | dftracer-analyzer | ok | 1 | 0 | 0 | 284.2 | 284.2 | 0.0000 | 0 | 0 | 0 |
| 3 | STEP 8: dftracer-optimizer | dftracer-optimizer | superseded | 1 | 0 | 0 | 69.0 | 69.0 | 0.0000 | 0 | 0 | 0 |
| 4 | STEP 8a: dftracer-optimizer-communication | dftracer-optimizer-communication | superseded | 1 | 0 | 0 | 19.3 | 19.3 | 0.0000 | 0 | 0 | 0 |
| 5 | STEP 8b: dftracer-optimizer-compute | dftracer-optimizer-compute | superseded | 1 | 0 | 0 | 10.3 | 10.3 | 0.0000 | 0 | 0 | 0 |
| 6 | STEP 8c: dftracer-optimizer-io | dftracer-optimizer-io | superseded | 1 | 0 | 0 | 13.2 | 13.2 | 0.0000 | 0 | 0 | 0 |
| 7 | STEP 8d: dftracer-optimizer-memory | dftracer-optimizer-memory | ok | 1 | 0 | 0 | 63.7 | 63.7 | 0.0000 | 0 | 0 | 0 |
