# Pipeline performance report — scaffold/20260709_081340

## Summary

- **Cost:** $0.0100 across 1 API calls
- **Tokens:** 0 total — 0 in, 0 out, 0 cache-read, 0 cache-write
- **Time:** 29525.3 s wall, 13754.7 s inside steps
- **Steps:** 11 (9 succeeded, 2 failed, 0 running)
- **Attempts:** 16 tries, 5 retries, 2 failed
- **Tools:** 0 calls (0 MCP), 0 failed, 0.0 s total
- **API errors:** 0 · **Compactions:** 0
- **MLflow:** http://127.0.0.1:5001/#/experiments/1/runs/&lt;mlflow-run-id&gt;

## Per-step

| # | Step | Agent | Status | Tries | Retries | Failed | Exec (s) | Wall (s) | Cost (USD) | Tokens | API | Tools |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | main | — | running | 1 | 0 | 0 | 29525.3 | 29525.3 | 0.0100 | 0 | 1 | 0 |
| 1 | STEP 0: dftracer-pipeline-planner (route+plan) | dftracer-pipeline-planner | ok | 1 | 0 | 0 | 106.4 | 106.4 | 0.0000 | 0 | 0 | 0 |
| 2 | STEP 1: dftracer-session-setup | dftracer-session-setup | ok | 1 | 0 | 0 | 281.2 | 281.2 | 0.0000 | 0 | 0 | 0 |
| 3 | STEP 3: dftracer-build-dftracer | dftracer-build-dftracer | ok | 2 | 1 | 0 | 1797.3 | 6779.6 | 0.0000 | 0 | 0 | 0 |
| 4 | STEP 4: dftracer-annotate-python | dftracer-annotate-python | ok | 2 | 1 | 0 | 1255.5 | 6729.0 | 0.0000 | 0 | 0 | 0 |
| 5 | STEP 5: dftracer-validate-python | dftracer-validate-python | ok | 1 | 0 | 0 | 1401.3 | 1401.3 | 0.0000 | 0 | 0 | 0 |
| 6 | STEP 5: dftracer-build-smoke | dftracer-build-smoke | failed | 1 | 0 | 1 | 2394.7 | 2394.7 | 0.0000 | 0 | 0 | 0 |
| 7 | STEP 6: dftracer-tracer | dftracer-tracer | failed | 2 | 1 | 1 | 3417.3 | 3683.2 | 0.0000 | 0 | 0 | 0 |
| 8 | STEP 7: dftracer-analyzer then dftracer-diagnoser | dftracer-analyzer | ok | 2 | 1 | 0 | 346.3 | 9763.8 | 0.0000 | 0 | 0 | 0 |
| 9 | STEP 8: dftracer-optimizer | dftracer-optimizer | ok | 1 | 0 | 0 | 1138.3 | 1138.3 | 0.0000 | 0 | 0 | 0 |
| 10 | STEP 9: dftracer-privacy-guard | dftracer-privacy-guard | ok | 1 | 0 | 0 | 359.2 | 359.2 | 0.0000 | 0 | 0 | 0 |
| 11 | ## STEP 8: dftracer-optimizer | dftracer-optimizer | ok | 2 | 1 | 0 | 1257.2 | 1257.2 | 0.0000 | 0 | 0 | 0 |

## Rework (retries and failed attempts)

### STEP 3: dftracer-build-dftracer

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | ok | 844.4 | — |
| 2 | ok | 952.9 | — |

### STEP 4: dftracer-annotate-python

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | ok | 361.7 | — |
| 2 | ok | 893.8 | — |

### STEP 5: dftracer-build-smoke

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | failed | 2394.7 | dftracer's compiled-in ROCProfiler/HIP interception (librocprofiler-sdk.so.1 linked into dftracer.dftracer.so) breaks torch's HIP/CUDA runtime init (RuntimeErro |

### STEP 6: dftracer-tracer

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | completed | 2731.2 | — |
| 2 | failed | 686.1 | Multi-rank baseline cannot execute: dataset insufficient for distributed training. Attempts: 8N (failed: 28 train<32), 4N (failed: 12 val<16). App's data loadin |

### STEP 7: dftracer-analyzer then dftracer-diagnoser

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | ok | 183.9 | — |
| 2 | ok | 162.5 | — |

### ## STEP 8: dftracer-optimizer

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | superseded | 880.0 | — |
| 2 | ok | 377.2 | — |


> **Note:** cost from the OTEL counters ($0.0000) differs from the sum of `api_request` events ($0.0100) by $-0.0100. Some log events were dropped or are still in flight; per-step attribution may under-count by that amount.
