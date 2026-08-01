# Pipeline performance report — megatron_deepspeed/20260727_164229

## Summary

- **Cost:** $0.0000 across 0 API calls
- **Tokens:** 0 total — 0 in, 0 out, 0 cache-read, 0 cache-write
- **Time:** 11714.8 s wall, 5243.2 s inside steps
- **Steps:** 4 (1 succeeded, 0 failed, 1 running)
- **Attempts:** 4 tries, 0 retries, 0 failed
- **Tools:** 0 calls (0 MCP), 0 failed, 0.0 s total
- **API errors:** 0 · **Compactions:** 0
- **MLflow:** http://127.0.0.1:20002/#/experiments/1/runs/&lt;mlflow-run-id&gt;

## Per-step

| # | Step | Agent | Status | Tries | Retries | Failed | Exec (s) | Wall (s) | Cost (USD) | Tokens | API | Tools |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | STEP: dftracer-build-app (BERT dataset prep) | dftracer-build-app | superseded | 1 | 0 | 0 | 64.1 | 64.1 | 0.0000 | 0 | 0 | 0 |
| 2 | STEP: dftracer-annotator (BERT) | dftracer-annotator | superseded | 1 | 0 | 0 | 95.3 | 95.3 | 0.0000 | 0 | 0 | 0 |
| 3 | STEP 5: dftracer-annotate-python | dftracer-annotate-python | ok | 1 | 0 | 0 | 387.2 | 387.2 | 0.0000 | 0 | 0 | 0 |
| 4 | STEP: dftracer-build-smoke | dftracer-build-smoke | running | 1 | 0 | 0 | 4696.5 | 4696.5 | 0.0000 | 0 | 0 | 0 |
