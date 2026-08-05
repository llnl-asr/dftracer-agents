# Pipeline performance report — ygm/20260804_221748

## Summary

- **Cost:** $0.0000 across 0 API calls
- **Tokens:** 0 total — 0 in, 0 out, 0 cache-read, 0 cache-write
- **Time:** 14400.1 s wall, 3092.4 s inside steps
- **Steps:** 4 (3 succeeded, 0 failed, 0 running)
- **Attempts:** 7 tries, 3 retries, 3 failed
- **Tools:** 0 calls (0 MCP), 0 failed, 0.0 s total
- **API errors:** 0 · **Compactions:** 0

## Per-step

| # | Step | Agent | Status | Tries | Retries | Failed | Exec (s) | Wall (s) | Cost (USD) | Tokens | API | Tools |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | STEP: dftracer-annotate-cpp (ygm) | dftracer-annotate-cpp | ok | 1 | 0 | 0 | 400.2 | 400.2 | 0.0000 | 0 | 0 | 0 |
| 2 | STEP 4: dftracer-build-smoke | dftracer-build-smoke | ok | 3 | 2 | 2 | 1969.6 | 2864.0 | 0.0000 | 0 | 0 | 0 |
| 3 | STEP 2: dftracer-build-dftracer | dftracer-build-dftracer | superseded | 2 | 1 | 1 | 719.9 | 736.9 | 0.0000 | 0 | 0 | 0 |
| 4 | STEP: ygm-bench dftracer integration (verify+canonical rerun) | general-purpose | ok | 1 | 0 | 0 | 2.6 | 2.6 | 0.0000 | 0 | 0 | 0 |

## Rework (retries and failed attempts)

### STEP 4: dftracer-build-smoke

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | blocked | 169.5 | dftracer not installed in this session (no venv/, no artifacts/dftracer_install.log, no libdftracer_core.so anywhere under the workspace); session_build_annotat |
| 2 | failed | 832.1 | Blocked by non-annotation toolchain bug: Cray clang 20.1.6 + spdlog-bundled fmt (fetched via FetchContent) fails with 'call to consteval function ... is not a c |
| 3 | ok | 968.0 | — |

### STEP 2: dftracer-build-dftracer

| Attempt | Status | Duration (s) | Error |
|---:|---|---:|---|
| 1 | failed | 19.9 | git clone https auth failed (gnome-ssh-askpass can't open display) |
| 2 | superseded | 700.0 | — |

