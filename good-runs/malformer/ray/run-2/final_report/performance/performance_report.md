# Pipeline performance report — ray_molformer/20260725_000436

## Summary

- **Cost:** $0.0000 across 0 API calls
- **Tokens:** 0 total — 0 in, 0 out, 0 cache-read, 0 cache-write
- **Time:** 4583.8 s wall, 1189.0 s inside steps
- **Steps:** 7 (3 succeeded, 0 failed, 1 running)
- **Attempts:** 8 tries, 1 retries, 0 failed
- **Tools:** 0 calls (0 MCP), 0 failed, 0.0 s total
- **API errors:** 0 · **Compactions:** 0
- **MLflow:** http://127.0.0.1:10002/#/experiments/1/runs/&lt;mlflow-run-id&gt;

## Per-step

| # | Step | Agent | Status | Tries | Retries | Failed | Exec (s) | Wall (s) | Cost (USD) | Tokens | API | Tools |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | dftracer-analyzer (baseline_4node) | dftracer-analyzer | ok | 1 | 0 | 0 | 575.0 | 575.0 | 0.0000 | 0 | 0 | 0 |
| 2 | STEP 7: dftracer-optimizer | dftracer-optimizer | ok | 2 | 1 | 0 | 205.6 | 3541.7 | 0.0000 | 0 | 0 | 0 |
| 3 | STEP 10a: dftracer-optimizer-compute | dftracer-optimizer-compute | superseded | 1 | 0 | 0 | 23.7 | 23.7 | 0.0000 | 0 | 0 | 0 |
| 4 | STEP 10c: dftracer-optimizer-communication | dftracer-optimizer-communication | superseded | 1 | 0 | 0 | 28.8 | 28.8 | 0.0000 | 0 | 0 | 0 |
| 5 | STEP 10b: dftracer-optimizer-io | dftracer-optimizer-io | superseded | 1 | 0 | 0 | 16.6 | 16.6 | 0.0000 | 0 | 0 | 0 |
| 6 | STEP 10d: dftracer-optimizer-memory | dftracer-optimizer-memory | ok | 1 | 0 | 0 | 326.8 | 326.8 | 0.0000 | 0 | 0 | 0 |
| 7 | STEP FINAL: dftracer-report | dftracer-report | running | 1 | 0 | 0 | 12.5 | 12.5 | 0.0000 | 0 | 0 | 0 |

## Rework (retries and failed attempts)

### STEP 7: dftracer-optimizer

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | superseded | 203.8 | — |
| 2 | ok | 1.8 | — |

