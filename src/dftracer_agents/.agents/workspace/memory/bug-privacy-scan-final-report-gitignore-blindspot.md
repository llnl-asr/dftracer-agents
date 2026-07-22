---
name: bug-privacy-scan-final-report-gitignore-blindspot
description: RESOLVED: privacy_scan/privacy_redact silently never scanned final_report/ because the entire gitignored session workspace was excluded via git check-ignore — fixed with an explicit final_report/ carve-out
metadata:
  type: feedback
---

Root cause: `privacy_tools.py`'s `_is_ignored()` excludes any file `git check-ignore` reports as
ignored, to avoid scanning staging/temp files that would never ship. But the ENTIRE
`workspaces/<session>/` tree is gitignored per project policy (it's meant to hold real resolved
paths for the live session) — which meant `final_report/`, the one artifact inside that tree
that policy explicitly requires to be scanned (it gets copied/pushed elsewhere), was ALSO
silently excluded. `privacy_scan`/`privacy_redact` would report "clean" or do nothing at all
against a `final_report/` path, with zero visibility into the fact that 0 files were actually
scanned.

**Discovered:** an agent building a PECAN session's final_report/ ran `privacy_scan`, got
"clean," trusted it, then manually grepped anyway per the mandatory rule and found real leaks
(absolute session path, username, flux job IDs, hostnames) that the tool had silently missed.

**Fix (code-level, not just documented):** added `_is_final_report(path)` — checks
`"final_report" in path.parts` — and short-circuits `_is_ignored()` to always return `False`
for any such path, regardless of git-ignore status. Verified: after the fix, `privacy_scan`
against a real `final_report/` correctly found 78 files (vs 0 before) and 3 real remaining
leaks (`$HOME` source path, VAST dataset paths, an mlflow run_id) that a prior
manual `sed`-based redaction pass had missed; `privacy_redact` fixed them, re-scan came back
genuinely clean.

**How to apply:** never trust `privacy_scan`'s "clean" result at face value for a path inside a
gitignored tree — check `files_scanned` in the response (or the old bug's behavior: 0 files
touched with no error) before trusting a "clean" verdict. This specific gap is now fixed in
`src/dftracer_agents/mcp_tools/tools/session/privacy_tools.py`, but the general lesson —
verify a scan actually touched a nonzero number of files before trusting "clean" — applies to
any future scan-tool usage on a path this tool hasn't been explicitly taught to reach into.
