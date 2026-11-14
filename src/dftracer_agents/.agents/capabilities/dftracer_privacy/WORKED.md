# What worked: dftracer_privacy

## privacy/end-of-session/steps
`privacy_scan()` -> `privacy_redact(dry_run=True)` -> user confirms -> `privacy_redact()` -> `privacy_scan()` clean -> `privacy_suspects()` triage -> `privacy_add_pattern(...)` for each real new class (with near-misses such as `torch==2.10.0+rocm710`, `/p/lustre5/$USER/x`, a `**Citation:**` line in must_not_change) -> redact + scan again; then `dftracer_privacy_check([<absolute final_report path>])`.

## tool:privacy_redact
Patches and diffs are redacted with preserve_structure automatically (suffix .patch/.diff); check `wc -l`, `grep -c '^@@'` and `/` per header line before and after.

## tool:privacy_add_pattern
`privacy_add_pattern(name="slurm-jobid", regex=..., replacement="<slurm-jobid>", must_redact=[real samples], must_not_change=[near-misses], structure_safe=True, note=<the corner case>)`; the rule lands in `.agents/workspace/privacy_patterns.yaml` only if every sample behaves.
