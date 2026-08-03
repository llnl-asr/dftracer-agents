# Pipeline performance report — af3/20260729_044330

## Summary

- **Cost:** $0.0000 across 0 API calls
- **Tokens:** 0 total — 0 in, 0 out, 0 cache-read, 0 cache-write
- **Time:** 6446.3 s wall, 4605.7 s inside steps
- **Steps:** 6 (4 succeeded, 1 failed, 1 running)
- **Attempts:** 7 tries, 1 retries, 1 failed
- **Tools:** 0 calls (0 MCP), 0 failed, 0.0 s total
- **API errors:** 0 · **Compactions:** 0
- **MLflow:** http://127.0.0.1:20002/#/experiments/1/runs/&lt;mlflow-run-id&gt;

## Per-step

| # | Step | Agent | Status | Tries | Retries | Failed | Exec (s) | Wall (s) | Cost (USD) | Tokens | API | Tools |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | STEP 1: dftracer-session-setup | dftracer-session-setup | ok | 1 | 0 | 0 | 355.2 | 355.2 | 0.0000 | 0 | 0 | 0 |
| 2 | STEP 2: dftracer-build-app | dftracer-build-app | ok | 1 | 0 | 0 | 648.8 | 648.8 | 0.0000 | 0 | 0 | 0 |
| 3 | STEP 3: dftracer-build-dftracer | dftracer-build-dftracer | failed | 1 | 0 | 1 | 712.2 | 712.2 | 0.0000 | 0 | 0 | 0 |
| 4 | ## STEP 4: dftracer-annotator | dftracer-annotator | ok | 2 | 1 | 0 | 124.2 | 975.6 | 0.0000 | 0 | 0 | 0 |
| 5 | STEP 3: dftracer-annotate-python | dftracer-annotate-python | ok | 1 | 0 | 0 | 461.1 | 461.1 | 0.0000 | 0 | 0 | 0 |
| 6 | ## STEP 5: dftracer-build-smoke | dftracer-build-smoke | running | 1 | 0 | 0 | 2304.1 | 2304.1 | 0.0000 | 0 | 0 | 0 |

## Rework (retries and failed attempts)

### STEP 3: dftracer-build-dftracer

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | failed | 712.2 | dftracer installed but tracing non-functional: C extension loads but no traces written, likely due to MPI being auto-enabled for a single-process (non-MPI) app. |

### ## STEP 4: dftracer-annotator

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | superseded | 120.5 | — |
| 2 | ok | 3.7 | — |

