# Pipeline performance report — ior/20260724_175545

## Summary

- **Cost:** $0.0000 across 0 API calls
- **Tokens:** 0 total — 0 in, 0 out, 0 cache-read, 0 cache-write
- **Time:** 214658.4 s wall, 4398.7 s inside steps
- **Steps:** 8 (2 succeeded, 0 failed, 1 running)
- **Attempts:** 15 tries, 7 retries, 3 failed
- **Tools:** 0 calls (0 MCP), 0 failed, 0.0 s total
- **API errors:** 0 · **Compactions:** 0
- **MLflow:** http://127.0.0.1:20002/#/experiments/1/runs/&lt;mlflow-run-id&gt;

## Per-step

| # | Step | Agent | Status | Tries | Retries | Failed | Exec (s) | Wall (s) | Cost (USD) | Tokens | API | Tools |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | STEP 5: dftracer-optimizer | dftracer-optimizer | superseded | 1 | 0 | 0 | 49.4 | 49.4 | 0.0000 | 0 | 0 | 0 |
| 2 | STEP 5a: dftracer-optimizer-io | dftracer-optimizer-io | superseded | 2 | 1 | 0 | 54.8 | 4806.3 | 0.0000 | 0 | 0 | 0 |
| 3 | STEP 5c: dftracer-optimizer-communication | dftracer-optimizer-communication | superseded | 1 | 0 | 0 | 18.3 | 18.3 | 0.0000 | 0 | 0 | 0 |
| 4 | STEP 5b: dftracer-optimizer-compute | dftracer-optimizer-compute | superseded | 1 | 0 | 0 | 23.1 | 23.1 | 0.0000 | 0 | 0 | 0 |
| 5 | STEP 5d: dftracer-optimizer-memory | dftracer-optimizer-memory | ok | 1 | 0 | 0 | 244.6 | 244.6 | 0.0000 | 0 | 0 | 0 |
| 6 | STEP 6: dftracer-build-smoke | dftracer-build-smoke | ok | 1 | 0 | 0 | 949.9 | 949.9 | 0.0000 | 0 | 0 | 0 |
| 7 | STEP 7: dftracer-tracer | dftracer-tracer | running | 7 | 6 | 3 | 3045.3 | 4717.0 | 0.0000 | 0 | 0 | 0 |
| 8 | STEP 5e: post-optimization trace | dftracer-tracer | superseded | 1 | 0 | 0 | 13.3 | 13.3 | 0.0000 | 0 | 0 | 0 |

## Rework (retries and failed attempts)

### STEP 5a: dftracer-optimizer-io

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | superseded | 26.7 | — |
| 2 | superseded | 28.2 | — |

### STEP 7: dftracer-tracer

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | superseded | 947.5 | — |
| 2 | failed | 3.7 | Transformers library ImportError: cannot import 'GeneralInterface'. Empty trace files (0 bytes) produced. App dependency mismatch requires VENV rebuild. |
| 3 | superseded | 263.3 | — |
| 4 | failed | 413.8 | Training failed: hardcoded path $HOME/molformer2/pubchem_descriptor_stats.npz not found; file exists at $PROJECT_ROOT/workspace |
| 5 | failed | 655.2 | Second distinct new failure: pandas/pyarrow numpy dtype conversion error prevents training from running; trace files empty (0 bytes). After fixing the first new |
| 6 | superseded | 747.8 | — |
| 7 | running | 14.0 | — |

