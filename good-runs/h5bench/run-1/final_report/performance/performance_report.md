# Pipeline performance report — h5bench/20260710_061131

## Summary

- **Cost:** $0.0000 across 0 API calls
- **Tokens:** 0 total — 0 in, 0 out, 0 cache-read, 0 cache-write
- **Time:** 6381.4 s wall, 4003.5 s inside steps
- **Steps:** 7 (2 succeeded, 2 failed, 1 running)
- **Attempts:** 9 tries, 2 retries, 3 failed
- **Tools:** 0 calls (0 MCP), 0 failed, 0.0 s total
- **API errors:** 0 · **Compactions:** 0
- **MLflow:** http://127.0.0.1:7002/#/experiments/1/runs/&lt;mlflow-run-id&gt;

## Per-step

| # | Step | Agent | Status | Tries | Retries | Failed | Exec (s) | Wall (s) | Cost (USD) | Tokens | API | Tools |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | STEP 2: dftracer-build-dftracer | dftracer-build-dftracer | superseded | 1 | 0 | 0 | 12.6 | 12.6 | 0.0000 | 0 | 0 | 0 |
| 2 | STEP 2: dftracer-build-app | dftracer-build-app | superseded | 1 | 0 | 0 | 1427.9 | 1427.9 | 0.0000 | 0 | 0 | 0 |
| 3 | STEP 3: dftracer-build-app | dftracer-build-app | ok | 1 | 0 | 0 | 306.5 | 306.5 | 0.0000 | 0 | 0 | 0 |
| 4 | STEP 2: dftracer-annotate-c | dftracer-annotate-c | ok | 2 | 1 | 0 | 988.1 | 1226.0 | 0.0000 | 0 | 0 | 0 |
| 5 | STEP 3: dftracer-build-smoke | dftracer-build-smoke | blocked | 2 | 1 | 2 | 398.2 | 1226.2 | 0.0000 | 0 | 0 | 0 |
| 6 | STEP 3: dftracer-build-dftracer-develop-retry | dftracer-build-dftracer-develop-retry | failed | 1 | 0 | 1 | 428.3 | 428.3 | 0.0000 | 0 | 0 | 0 |
| 7 | STEP N: dftracer-build-dftracer-mpiio-fix | dftracer-build-dftracer | running | 1 | 0 | 0 | 441.9 | 441.9 | 0.0000 | 0 | 0 | 0 |

## Rework (retries and failed attempts)

### STEP 2: dftracer-annotate-c

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | ok | 354.5 | — |
| 2 | ok | 633.6 | — |

### STEP 3: dftracer-build-smoke

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | failed | 64.8 | annotated/source/ is missing the actual h5bench codebase (no CMakeLists.txt, no src/); it only contains stray scripts/traces/record subdirs. STEP 2 annotation o |
| 2 | blocked | 333.4 | Build succeeded (all 7 binaries built, correctly linked against session HDF5 1.14.5 and libdftracer_core), but functional smoke test reveals a runtime ABI misma |

### STEP 3: dftracer-build-dftracer-develop-retry

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | failed | 428.3 | HDF5-async ABI mismatch confirmed as unfixed in develop HEAD; root cause identified as brahma wrapper signature mismatch with HDF5 1.14.5 async function macros |

