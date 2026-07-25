# Pipeline performance report — ray_molformer/20260725_000436

## Summary

- **Cost:** $0.0000 across 0 API calls
- **Tokens:** 0 total — 0 in, 0 out, 0 cache-read, 0 cache-write
- **Time:** 14399.8 s wall, 11726.8 s inside steps
- **Steps:** 7 (3 succeeded, 1 failed, 1 running)
- **Attempts:** 7 tries, 0 retries, 1 failed
- **Tools:** 0 calls (0 MCP), 0 failed, 0.0 s total
- **API errors:** 0 · **Compactions:** 0
- **MLflow:** http://127.0.0.1:20002/#/experiments/1/runs/&lt;mlflow-run-id&gt;

## Per-step

| # | Step | Agent | Status | Tries | Retries | Failed | Exec (s) | Wall (s) | Cost (USD) | Tokens | API | Tools |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | STEP 1: dftracer-build-app | dftracer-build-app | superseded | 1 | 0 | 0 | 97.4 | 97.4 | 0.0000 | 0 | 0 | 0 |
| 2 | STEP 3: dftracer-build-app | dftracer-build-system-pip | failed | 1 | 0 | 1 | 792.9 | 792.9 | 0.0000 | 0 | 0 | 0 |
| 3 | STEP 4: dftracer-build-dftracer | dftracer-build-dftracer | ok | 1 | 0 | 0 | 2630.0 | 2630.0 | 0.0000 | 0 | 0 | 0 |
| 4 | STEP 5: dftracer-annotator | dftracer-annotator | superseded | 1 | 0 | 0 | 1263.3 | 1263.3 | 0.0000 | 0 | 0 | 0 |
| 5 | STEP 4: dftracer-annotate-python | dftracer-annotate-python | ok | 1 | 0 | 0 | 1848.5 | 1848.5 | 0.0000 | 0 | 0 | 0 |
| 6 | STEP: dftracer-validate-python | dftracer-validate-python | ok | 1 | 0 | 0 | 2764.8 | 2764.8 | 0.0000 | 0 | 0 | 0 |
| 7 | STEP 6: dftracer-build-smoke | dftracer-build-smoke | running | 1 | 0 | 0 | 2330.1 | 2330.1 | 0.0000 | 0 | 0 | 0 |

## Rework (retries and failed attempts)

### STEP 3: dftracer-build-app

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | failed | 792.9 | Build timeout - PyTorch/DeepChem installation failed on network downloads; Dask/Ray installed |

