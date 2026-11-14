# What worked: dftracer_profiling

## tool:profile_step_begin
`profile_step_begin(step="STEP 3: dftracer-annotator", agent="dftracer-annotator", notes=...)` after loading the plan section; on failure `profile_step_end(step=<same>, status="lint_error", error=...)` then `profile_step_begin(step=<same>)` records a retry.

## tool:profile_report
A few seconds after the last step: `profile_report()` writes `performance/performance_report.md`, `summary.json`, `steps/<n>-<step>.json`; then `dftracer_profiling_check(run_id)`.

## profiling/telemetry/remote-ssh
Exports in `$HOME/.vscode-server/server-env-setup` (CLAUDE_CODE_ENABLE_TELEMETRY=1, OTEL_LOGS_EXPORTER=otlp, OTEL_METRICS_EXPORTER=otlp, OTEL_EXPORTER_OTLP_PROTOCOL=http/json, OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318), Kill VS Code Server on Host, reconnect; `profile_status` then shows events_seen > 0.
