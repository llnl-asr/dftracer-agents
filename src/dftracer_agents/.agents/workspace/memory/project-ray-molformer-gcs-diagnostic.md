---
name: project-ray-molformer-gcs-diagnostic
description: Ray MoLFormer dftracer pipeline session complete; final_report detail + automatic PDF + completeness/README validation + patches anonymization + mandatory pipeline stage, all landed persistently
metadata:
  type: project
---

**Canonical home:** see the `dftracer-privacy-guard` skill (the `final_report/`
gitignore-blindspot fix this session's finding #7 relies on) — the `final_report.py`
tooling fixes themselves live only in the tool source and the `dftracer-report`/
`dftracer-pipeline-planner` agent templates, not a dedicated skill file.

# Ray MoLFormer dftracer pipeline — session complete

Session `ray_molformer/20260725_000436` (Tuolumne, Ray 2.48.0 / MI300A) is fully complete: all 13 pipeline steps done, `final_report/` validated (independent reproduction within noise), completeness-checked (`ok: true`), README-smoke-tested (`ok: true`), and privacy-clean.

**Result**: bf16 autocast measured no_change/slightly-slower; 4-node scaling regressed vs 2-node (dataset too small); a real NaN-loss bug (RDKit Gasteiger partial-charge NaN/inf) was found and fixed. Highest-value unmeasured next step: RCCL likely defaults to the 1GbE management NIC instead of the 200Gb Slingshot fabric (`NCCL_SOCKET_IFNAME=hsi0` untried, est. ~40% comm-bucket headroom).

**Tooling fixes landed this session** (user-requested, persistent), all in `src/dftracer_agents/mcp_tools/tools/session/final_report.py`:
1. `session_final_report` renders `REPORT.md` → `REPORT.pdf` automatically every call (pandoc, else pure-Python fallback) — `pdf: {generated, path, size_bytes}`.
2. Fixed `patches` derivation for single-shared-script apps (`_diff_files`/`_variant_source_files` diff `annotated/src/<name>_optN.py` against a shared base script when no per-run `source/` tree exists) and non-chained `from_baseline.record.diff` naming — previously silently `{}`.
3. `_validate_report_completeness` → `completeness: {ok, missing, warnings}`: checks all 11+ required REPORT.md sections, per-optimization code-block count, "what's next" language, non-empty patches.
4. `_anonymize_path_refs`: patch/diff files previously shipped the real absolute workspace path unmodified (unlike scripts) — now auto-stripped to `$WS`.
5. `_write_readme_smoke_test`/`_run_readme_smoke_test` → `readme_check: {ok, missing}`: writes `scripts/readme_smoke_test.sh` (persisted, standalone-rerunnable) and runs it every call — checks README.md names every script, explains `config.ini`/`WORKSPACE_ROOT`, has a literal runnable command, and points back to `REPORT.md` for verification. README.md itself was rewritten this session with exact copy-pasteable commands per step (cp, edit config.ini, `bash scripts/install.sh`, `bash scripts/run_all.sh <alloc>`, single-case `flux proxy` invocation, expected-Job-Time comparison table, patches walkthrough).
6. `dftracer-report.yaml` strengthened to mandate checking `pdf.generated`/`completeness.ok`/`readme_check.ok` all true before considering a report done.
7. `dftracer-pipeline-planner.yaml` fixed — its mandatory-stage table previously stopped at stage 8 (`dftracer-optimizer`), never listing `dftracer-report`/`dftracer-privacy-guard` as required, so a plan could omit final_report assembly entirely. Added stage 9 (`dftracer-report`, gated on the 3 booleans above) and stage 10 (`dftracer-privacy-guard`) as MANDATORY, no-exception stages, plus matching STEP 9/10 template sections and an updated DISPATCH ORDER example — so final_report + its validation (incl. the README smoke test) is now always part of every planned pipeline, not an optional afterthought.

`agents_sync()` propagated all of this to claude/opencode/copilot.

**Confirms a known gap (still open, not fixed at the tool level)**: `privacy_scan()`'s default path set does NOT cover `final_report/` — must pass it explicitly as a `paths` arg every time; the STEP 10 planner template note now says this explicitly too.

See [[software-ray-molformer]] for the full technical root-cause chain (14+ fixes) and [[feedback-privacy-anonymous]] for the anonymization policy these final_report fixes enforce.
