# What worked: dftracer_report

## tool:session_final_report
Call with the three narrative bodies (report_md in the full 11-section structure, conversation_md, readme_md with exact commands), then require `pdf.generated`, `completeness.ok` and `readme_check.ok` all true; re-call after each fix. After the isolated validation passes, call once more with `validated=True` and a one-line `validation_notes` (e.g. "124.2s vs 124.0-124.6s expected band"), BEFORE the final privacy pass.

## tool:session_capture_run_record
At the end of every run-producing step: `session_capture_run_record(run_id, run_name="opt2", prev_run_name="opt1", run_script=<wrapper>, run_log=<log>, notes=<what changed and why>)`; writes `<run>/record/`, `<run>/scripts/run.sh`, `artifacts/<run>_run.log`, `patches/from_<prev>.record.diff`.

## report/finalize/order
customize final_report/ -> session_final_report (gates) -> session_verify_isolation static/launch/status -> session_final_report(validated=True) -> final fixes -> agents_sync -> memory_write -> privacy_redact + privacy_scan (trees and final_report/) last -> dftracer_report_check pass (2026-08-02).
