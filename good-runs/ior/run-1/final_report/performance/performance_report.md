# Pipeline performance report — ior/20260710_172024

## Summary

- **Cost:** $0.0000 across 0 API calls
- **Tokens:** 0 total — 0 in, 0 out, 0 cache-read, 0 cache-write
- **Time:** 16555.8 s wall, 10198.2 s inside steps
- **Steps:** 15 (7 succeeded, 1 failed, 0 running)
- **Attempts:** 23 tries, 8 retries, 1 failed
- **Tools:** 0 calls (0 MCP), 0 failed, 0.0 s total
- **API errors:** 0 · **Compactions:** 0
- **MLflow:** http://127.0.0.1:7002/#/experiments/1/runs/dfe6b36183864ec7afe8cfb6b292b930

## Per-step

| # | Step | Agent | Status | Tries | Retries | Failed | Exec (s) | Wall (s) | Cost (USD) | Tokens | API | Tools |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | STEP 2: dftracer-build-app | dftracer-build-app | ok | 1 | 0 | 0 | 195.7 | 195.7 | 0.0000 | 0 | 0 | 0 |
| 2 | STEP 3: dftracer-annotator | dftracer-annotator | ok | 1 | 0 | 0 | 490.0 | 490.0 | 0.0000 | 0 | 0 | 0 |
| 3 | STEP 4: dftracer-build-smoke | dftracer-build-smoke | ok | 1 | 0 | 0 | 219.4 | 219.4 | 0.0000 | 0 | 0 | 0 |
| 4 | STEP 5: dftracer-tracer | dftracer-tracer | superseded | 5 | 4 | 0 | 1367.1 | 6241.6 | 0.0000 | 0 | 0 | 0 |
| 5 | STEP 2: dftracer-tracer | dftracer-tracer | ok | 1 | 0 | 0 | 421.6 | 421.6 | 0.0000 | 0 | 0 | 0 |
| 6 | STEP: HDF5 Baseline Run with Iterations (10-min target) | dftracer-tracer | superseded | 1 | 0 | 0 | 69.3 | 69.3 | 0.0000 | 0 | 0 | 0 |
| 7 | STEP 5: dftracer-analyzer / dftracer-diagnoser | dftracer-tracer | superseded | 2 | 1 | 0 | 1036.1 | 2168.5 | 0.0000 | 0 | 0 | 0 |
| 8 | STEP 6: dftracer-optimizer | dftracer-optimizer | ok | 2 | 1 | 0 | 1831.9 | 8932.3 | 0.0000 | 0 | 0 | 0 |
| 9 | STEP: write baseline calibration and collection | dftracer-tracer | partial | 2 | 1 | 1 | 74.3 | 2766.5 | 0.0000 | 0 | 0 | 0 |
| 10 | STEP: HDF5 OOM Root-Cause and Configuration Calibration | dftracer-tracer | superseded | 1 | 0 | 0 | 37.2 | 37.2 | 0.0000 | 0 | 0 | 0 |
| 11 | STEP: dftracer-validate-c fix aiori-HDF5.c END/START imbalance | dftracer-validate-c | superseded | 1 | 0 | 0 | 763.4 | 763.4 | 0.0000 | 0 | 0 | 0 |
| 12 | STEP: Fix HDF5_CHECK annotation in aiori-HDF5.c | dftracer-fix-hdf5-check | superseded | 1 | 0 | 0 | 126.7 | 126.7 | 0.0000 | 0 | 0 | 0 |
| 13 | STEP 2: dftracer-analyzer | dftracer-analyzer | superseded | 1 | 0 | 0 | 8.0 | 8.0 | 0.0000 | 0 | 0 | 0 |
| 14 | STEP 6: dftracer-analyzer then dftracer-diagnoser | dftracer-analyzer | ok | 2 | 1 | 0 | 1729.9 | 1729.9 | 0.0000 | 0 | 0 | 0 |
| 15 | STEP 7: dftracer-optimizer | dftracer-optimizer | ok | 1 | 0 | 0 | 1827.7 | 1827.7 | 0.0000 | 0 | 0 | 0 |

## Rework (retries and failed attempts)

### STEP 5: dftracer-tracer

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | superseded | 571.8 | — |
| 2 | superseded | 112.3 | — |
| 3 | ok | 193.0 | — |
| 4 | superseded | 276.6 | — |
| 5 | superseded | 213.5 | — |

### STEP 5: dftracer-analyzer / dftracer-diagnoser

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | ok | 692.4 | — |
| 2 | superseded | 343.8 | — |

### STEP 6: dftracer-optimizer

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | ok | 417.9 | — |
| 2 | ok | 1414.0 | — |

### STEP: write baseline calibration and collection

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | superseded | 22.7 | — |
| 2 | partial | 51.6 | Calibration successful (DIM_1=2097152 confirmed: 576GB/2.7min). Production baseline collection encountered flux job cancellations. 5 reps partially complete (96 |

### STEP 6: dftracer-analyzer then dftracer-diagnoser

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | superseded | 762.0 | — |
| 2 | ok | 967.9 | — |

