# Pipeline performance report — 1000genome_workflow/20260725_203603

## Summary

- **Cost:** $0.0000 across 0 API calls
- **Tokens:** 0 total — 0 in, 0 out, 0 cache-read, 0 cache-write
- **Time:** 10079.9 s wall, 1578.8 s inside steps
- **Steps:** 3 (3 succeeded, 0 failed, 0 running)
- **Attempts:** 5 tries, 2 retries, 0 failed
- **Tools:** 0 calls (0 MCP), 0 failed, 0.0 s total
- **API errors:** 0 · **Compactions:** 0
- **MLflow:** http://127.0.0.1:20002/#/experiments/1/runs/<flux-jobid>

## Per-step

| # | Step | Agent | Status | Tries | Retries | Failed | Exec (s) | Wall (s) | Cost (USD) | Tokens | API | Tools |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | STEP 1: dftracer-session-setup | dftracer-session-setup | ok | 1 | 0 | 0 | 90.2 | 90.2 | 0.0000 | 0 | 0 | 0 |
| 2 | STEP 2: dftracer-build-app | dftracer-build-app | ok | 1 | 0 | 0 | 436.2 | 436.2 | 0.0000 | 0 | 0 | 0 |
| 3 | STEP 3: dftracer-build-dftracer | dftracer-build-dftracer | ok | 3 | 2 | 0 | 1052.3 | 6796.0 | 0.0000 | 0 | 0 | 0 |

## Rework (retries and failed attempts)

### STEP 3: dftracer-build-dftracer

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | superseded | 48.3 | — |
| 2 | ok | 485.3 | — |
| 3 | ok | 518.7 | — |

