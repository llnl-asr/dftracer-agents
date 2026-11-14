"""dftracer_report: the final pipeline stage. Capture each run's record before it is overwritten, assemble the
self-contained, reproducible final_report/ (REPORT.md/.pdf, README.md, CONVERSATION.md, config.ini, scripts, patches,
plan, performance), and verify it deterministically: structure, config, privacy, script syntax and loader, every run
scripted, patches, README smoke test, and the mandatory REPORT.md sections."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List

from dftracer_agents.mcp_tools.tools.session.final_report import (
    _REQUIRED_REPORT_SECTIONS,
    _session_capture_run_record_impl,
    _session_final_report_impl,
    _write_readme_smoke_test,
    verify_final_report,
)
from dftracer_agents.mcp_tools.tools.session.workspace import _err, _ok, _ws

_FIX = {
    "structure": "call session_final_report (it writes config.ini, scripts/lib_load_config.sh, run_all.sh, install.sh, README.md, REPORT.md)",
    "config": "config.ini must define WORKSPACE_ROOT=; it is the one file allowed to carry real values",
    "privacy": "privacy_redact(paths=[<final_report>]) then privacy_scan; only config.ini may hold real paths; never call session_final_report again after the last redaction (it regenerates files)",
    "syntax": "fix the script bash -n names (hand edits), then re-verify",
    "loader": "make every run/install script source scripts/lib_load_config.sh instead of hard-coding paths",
    "runs": "add scripts/run_<run>.sh for every measured run (bespoke wrappers too) and call each from run_all.sh; session_capture_run_record at the end of every run step",
    "patches": "session_capture_run_record for the annotated run, or a source snapshot so patches/annotated.patch is produced",
    "readme": "README.md must name every script, explain config.ini/WORKSPACE_ROOT, give a literal runnable command and point back to REPORT.md",
    "report_sections": "add the missing REPORT.md sections (Report Structure of this skill), then re-call session_final_report",
    "pdf": "read the pdf.reason session_final_report returned (pandoc or the markdown-it-py+xhtml2pdf fallback) and re-call it",
}


def _report_verdict(final: Path, ws: Any = None, runs: Any = None) -> Dict[str, Any]:
    """verify_final_report plus the REPORT.md structure and the PDF, as {check, ok, detail, fix} items."""
    v = verify_final_report(final, ws, runs)
    checks: List[Dict[str, Any]] = [{"check": c["name"], "ok": c["ok"], "detail": c["detail"],
                                     "fix": "" if c["ok"] else _FIX.get(c["name"], "")} for c in v["checks"]]
    rp = final / "REPORT.md"
    text = rp.read_text(errors="ignore").lower() if rp.is_file() else ""
    missing = [s for s in _REQUIRED_REPORT_SECTIONS if s.lower() not in text]
    checks.append({"check": "report_sections", "ok": bool(text) and not missing,
                   "detail": ("missing: " + ", ".join(missing)) if missing else "all required sections present",
                   "fix": "" if text and not missing else _FIX["report_sections"]})
    pdf = final / "REPORT.pdf"
    checks.append({"check": "pdf", "ok": pdf.is_file() and pdf.stat().st_size > 0,
                   "detail": "REPORT.pdf present" if pdf.is_file() else "no REPORT.pdf",
                   "fix": "" if pdf.is_file() else _FIX["pdf"]})
    return {"status": "pass" if all(c["ok"] for c in checks) else "fail", "checks": checks, "reproduced": v["reproduced"]}


def register(mcp):
    @mcp.tool()
    def session_final_report_verify(run_id: str, runs: List[str] | None = None) -> str:
        """Verify a session's ``final_report/`` deterministically (does not trust the builder or ``validated``).

        Checks structure, config.ini (WORKSPACE_ROOT), privacy (no workspace path / user name / home or PFS user
        path / e-mail / known identifier in any file except config.ini), ``bash -n`` of every script, that run and
        install scripts source lib_load_config.sh, that every measured run (``runs`` or the session's
        baseline/opt<n>/variants) has ``scripts/run_<run>.sh`` called by run_all.sh, the annotated patch, and the
        README smoke test. Returns JSON ``{status: pass|fail, checks:[{name, ok, detail}], problems, reproduced}``;
        ``reproduced`` says whether REPORT.md records a passed self-contained reproduction run.
        """
        try:
            ws = _ws(run_id)
        except Exception as e:  # noqa: BLE001
            return _err(f"session_final_report_verify: {e}")
        final = ws / "final_report"
        if not final.is_dir():
            return _err(f"no final_report/ in this session: call session_final_report first")
        v = verify_final_report(final, ws, runs)
        return _ok(f"final_report verify: {v['status']}", verdict=v["status"], checks=v["checks"],
                   problems=v["problems"], reproduced=v["reproduced"], final_report_dir=v["final_report_dir"])

    @mcp.tool()
    def session_capture_run_record(
        run_id: str,
        run_name: str,
        prev_run_name: str = "",
        source_path: str = "",
        run_script: str = "",
        run_log: str = "",
        param_files: str = "",
        notes: str = "",
    ) -> str:
        """Capture everything needed to reconstruct a run, before it is overwritten.

        **Call this at the END of every run-producing step** (``annotated``,
        ``baseline``, each ``opt<n>``). Iterations overwrite build config, the
        parameter file, and the run wrapper in place, so the information is gone
        by the time ``session_final_report`` runs unless it was captured here.

        Writes ``<run_name>/record/{build_config,params,meta.json}``,
        ``<run_name>/scripts/run.sh``, copies the log to
        ``artifacts/<run_name>_run.log``, and (with *prev_run_name*) a
        ``patches/from_<prev>.record.diff`` showing the iteration's actual delta.

        Args:
            run_id: Session identifier returned by ``session_create``.
            run_name: Run label (``"annotated"``, ``"baseline"``, ``"opt1"``, ...).
            prev_run_name: Previous run to diff this record against.
            source_path: Source tree for this run. Defaults to ``annotated/source``.
            run_script: Path to the wrapper script used to launch the run.
            run_log: Path to the run's stdout/stderr log; copied into ``artifacts/``.
            param_files: Comma-separated parameter files (absolute, or relative to
                ``<source>/object/``). Defaults to ``object/flash.par`` if present.
            notes: Free-text description of what this iteration changed and why.

        Returns:
            JSON with ``record_dir``, the ``captured`` file lists, and
            ``record_patch_lines``.
        """
        return _session_capture_run_record_impl(
            run_id=run_id, run_name=run_name, prev_run_name=prev_run_name,
            source_path=source_path, run_script=run_script, run_log=run_log,
            param_files=param_files, notes=notes,
        )

    @mcp.tool()
    def session_final_report(
        run_id: str,
        report_md: str = "",
        conversation_md: str = "",
        readme_md: str = "",
        alloc_hint: str = "",
        validated: bool = False,
        validation_notes: str = "",
        overwrite: bool = False,
    ) -> str:
        """Assemble a self-contained, reproducible ``final_report/`` folder.

        Collects, from what is already on disk (never re-running the app):

        * ``config.ini`` — the ONLY place a real path/session value goes
          (``WORKSPACE_ROOT`` placeholder + app/compiler settings). Every
          script sources it via ``scripts/lib_load_config.sh`` instead of
          hardcoding a path, so nothing else here can leak a username or
          absolute user path (git-tracked deliverables must never embed PII).
        * ``scripts/`` — ``lib_load_config.sh`` (generic config loader),
          ``install.sh`` (rebuild deps + app), one ``run_<case>.sh`` per case
          (collected from ``tmp/`` and automatically re-pathed to use
          ``$WS``/``config.ini`` instead of the literal absolute path — see
          ``_anonymize_script``), and ``run_all.sh`` to drive them in order.
        * ``patches/`` — ``annotated.patch`` (baseline→annotated source) and,
          per optimization iteration, ``opt<n>.patch`` (source delta) plus
          ``opt<n>.config.diff`` (parameter-file / run-wrapper delta).
        * ``plan/`` — the final ``pipeline_plan.md`` that was executed, its
          changelog, and ``plan_evolution.diff`` vs the first tracked revision.
        * ``REPORT.md``, ``CONVERSATION.md``, ``README.md`` — narrative docs.
          Raw run/build logs are NOT copied in — they stay in the session
          workspace's ``artifacts/`` dir, out of the reproducible package.

        The three narrative documents are passed in by the caller: the agent
        knows what was found and why, and this tool must not invent results.

        **Mandatory before considering a session's final_report/ done:** run a
        self-contained validation — point ``OUTPUT_ROOT`` (in config.ini or
        the environment) at a scratch directory isolated from the session's
        own run data, fill in ``WORKSPACE_ROOT``, and execute
        ``scripts/run_all.sh <alloc-id>`` end to end using ONLY what's in
        ``final_report/``. Once it reproduces the reported results, re-call
        this tool with ``validated=True`` and a short ``validation_notes``
        summary — this stamps REPORT.md with a pass/fail marker instead of
        leaving reproducibility as an unverified claim.

        **``report_md`` detail bar (MANDATORY, not best-effort):** a short prose
        summary is not sufficient even for a session with many steps. Every
        baseline run gets its own row/section (not folded into one line), and
        every optimization considered — applied, applied-then-reverted, and
        not-applicable-with-reason — gets a row in an Optimization Ledger
        table (dimension | level | change | applied? | measured delta |
        replicates/significance | verdict). A rejected optimization (e.g. one
        that was tried and measured to make things worse) is exactly as
        reportable as a winning one — omitting it hides real work and risks
        someone re-trying it blind later. See the ``dftracer-report`` agent's
        "Report Structure" section for the full 10-section template this
        should follow.

        Args:
            run_id: Session identifier returned by ``session_create``.
            report_md: Markdown body for ``REPORT.md`` (what was done, results —
                see the detail-bar note above; follow the full structured
                template, not a condensed summary).
            conversation_md: Markdown body for ``CONVERSATION.md`` (narrative).
            readme_md: Markdown body for ``README.md`` — must give a reader who
                has ONLY this ``final_report/`` folder (no session workspace,
                no conversation history) exact, copy-pasteable commands: copy
                the folder, edit ``config.ini``'s ``WORKSPACE_ROOT``, the exact
                ``bash scripts/install.sh`` / ``bash scripts/run_all.sh
                <alloc-id>`` invocations, every script by name and what it
                does, and a pointer back to the ``REPORT.md`` numbers to
                compare against. ``readme_check`` (see Returns) fails if this
                is thin.
            alloc_hint: Default Flux allocation id baked into ``run_all.sh``.
            validated: Set True only after the self-contained validation above
                has actually been run and passed.
            validation_notes: One-line summary of what the validation run
                measured (e.g. "124.2s vs 124.0-124.6s expected band").
            overwrite: When False (default), omitting or blanking
                report_md/conversation_md/readme_md PRESERVES any existing
                REPORT.md/CONVERSATION.md/README.md content instead of
                overwriting it with a placeholder. Set True to force a
                from-scratch regeneration (placeholder included) even when
                a real file already exists.

        Returns:
            JSON with ``status``, ``final_report_dir``, the ``runs`` discovered,
            a ``patches`` map of filename→line count, the ``scripts``/``config``
            written, ``logs_copied``, ``validated``, ``pdf`` (PDF render result),
            ``completeness`` (``{ok, missing, warnings}`` — REPORT.md structure
            check), ``readme_check`` (``{ok, missing}`` — the README smoke test:
            does it name every script, explain config.ini/WORKSPACE_ROOT, give
            a real runnable command, and point back to REPORT.md?), and (if
            anything above is not yet true) a ``reminder`` naming exactly what
            to fix before calling this session done.
        """
        return _session_final_report_impl(
            run_id=run_id,
            report_md=report_md,
            conversation_md=conversation_md,
            readme_md=readme_md,
            alloc_hint=alloc_hint,
            validated=validated,
            validation_notes=validation_notes,
            overwrite=overwrite,
        )

    @mcp.tool()
    def dftracer_report_check(run_id: str = "", final_report_dir: str = "", runs: List[str] | None = None) -> dict:
        """Validate a final_report/ deterministically, never trusting the builder or its validated flag. Give run_id (checks
        <WS>/final_report against that session's runs) or an absolute final_report_dir (with runs, e.g. ["baseline",
        "opt1"]). Checks: structure, config.ini WORKSPACE_ROOT, privacy (no identifier outside config.ini), bash -n of every
        script, loader sourcing, every run scripted and called by run_all.sh, patches/annotated.patch when there is an
        annotated tree, README smoke test, every mandatory REPORT.md section (Executive Summary ... Remaining Work), and a
        REPORT.pdf. Use after session_final_report and again after the final privacy pass. Returns {status:
        pass|fail|inconclusive, checks: [{check, ok, detail, fix}], reproduced}."""
        ws = None
        if run_id.strip():
            ws = _ws(run_id)
            final = ws / "final_report"
        elif final_report_dir:
            final = Path(final_report_dir)
        else:
            return {"status": "inconclusive", "checks": [{"check": "input", "ok": False, "detail": "give run_id or final_report_dir",
                                                          "fix": "pass the session run_id"}]}
        if not final.is_dir():
            return {"status": "inconclusive", "checks": [{"check": "final_report_present", "ok": False,
                                                          "detail": "unknown run or no final_report/ yet",
                                                          "fix": "call session_final_report(run_id, report_md, conversation_md, readme_md) first"}]}
        return _report_verdict(final, ws, runs)

    @mcp.tool()
    def dftracer_report_selftest() -> dict:
        """Self-test dftracer_report_check on a minimal final_report/ fixture it writes to a temp dir (config.ini, loader,
        install/run/run_all scripts, the real README smoke test, README, REPORT.md with every section, a PDF stub): it must
        pass; a REPORT.md missing a section must fail on report_sections; a script with a syntax error must fail on syntax.
        Use to check the validator itself. Returns {status: pass|fail, detail}."""
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "final_report"
            sd = f / "scripts"
            sd.mkdir(parents=True)
            (f / "config.ini").write_text("WORKSPACE_ROOT=\nOUTPUT_ROOT=\n")
            (sd / "lib_load_config.sh").write_text("#!/bin/bash\nCFG=\"$(dirname \"${BASH_SOURCE[0]}\")/../config.ini\"\n")
            (sd / "install.sh").write_text("#!/bin/bash\nsource \"$(dirname \"$0\")/lib_load_config.sh\"\necho install\n")
            (sd / "run_baseline.sh").write_text("#!/bin/bash\nsource \"$(dirname \"$0\")/lib_load_config.sh\"\necho run\n")
            (sd / "run_all.sh").write_text("#!/bin/bash\nbash \"$(dirname \"$0\")/run_baseline.sh\"\n")
            _write_readme_smoke_test(sd)
            (f / "README.md").write_text(
                "# Reproduce\n\nEdit config.ini and set WORKSPACE_ROOT. Scripts: lib_load_config.sh, install.sh, run_baseline.sh, run_all.sh.\n\n"
                "```\nbash scripts/install.sh\nbash scripts/run_all.sh <alloc-id>\n```\n\nCompare with the numbers in REPORT.md.\n")
            report = "\n".join(f"## {i}. {s}\ntext\n" for i, s in enumerate(_REQUIRED_REPORT_SECTIONS, 1))
            (f / "REPORT.md").write_text(report)
            (f / "REPORT.pdf").write_bytes(b"%PDF-1.4 stub")
            good = dftracer_report_check(final_report_dir=str(f), runs=["baseline"])
            (f / "REPORT.md").write_text(report.replace("Remaining Work", "Later"))
            nosec = dftracer_report_check(final_report_dir=str(f), runs=["baseline"])
            (f / "REPORT.md").write_text(report)
            (sd / "run_baseline.sh").write_text("#!/bin/bash\nsource \"$(dirname \"$0\")/lib_load_config.sh\"\nif then\n")
            badsh = dftracer_report_check(final_report_dir=str(f), runs=["baseline"])
        failed = lambda v: [c["check"] for c in v["checks"] if not c["ok"]]
        ok = good["status"] == "pass" and failed(nosec) == ["report_sections"] and "syntax" in failed(badsh)
        return {"status": "pass" if ok else "fail",
                "detail": f"good={good['status']} {failed(good)} no_section={failed(nosec)} bad_script={failed(badsh)}"}
