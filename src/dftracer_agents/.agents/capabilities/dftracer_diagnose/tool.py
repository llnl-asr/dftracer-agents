"""dftracer_diagnose: score dfanalyzer checkpoints with DFDiagnoser into a ranked bottleneck list (diagnose, session_diagnose_bottlenecks) and validate the diagnosis deterministically (dftracer_diagnose_check)."""
from __future__ import annotations

import json
from dftracer_agents.mcp_tools.tools.dftracer.dfdiagnoser_service import _diagnose_via_api
from dftracer_agents.mcp_tools.tools.dftracer.dfdiagnoser_service import _diagnose_via_pandas
from dftracer_agents.mcp_tools.tools.dftracer.dfdiagnoser_service import _extract_bottlenecks
from dftracer_agents.mcp_tools.tools.dftracer.dfdiagnoser_service import _extract_bottlenecks_from_findings
from dftracer_agents.mcp_tools.tools.dftracer.dfdiagnoser_service import _load_raw_stats
from dftracer_agents.mcp_tools.tools.dftracer.dfdiagnoser_service import _load_scored_views
from dftracer_agents.mcp_tools.tools.optimizations.diagnose import _session_diagnose_bottlenecks_impl
from pathlib import Path
from typing import Optional

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from dftracer_agents.mcp_tools.tools.optimizations.strategies import _BUILTIN_REFS

# --------------------------------------------------------------------------- validator helpers (deterministic, offline)
_LEVELS = ("L1", "L2", "L3")
_SCOPE_LEVEL = {"workload": "L1", "software": "L2", "system": "L3"}
_ARXIV_RX = re.compile(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b")
_DOI_RX = re.compile(r"\b10\.\d{4,9}/\S+")
_URL_RX = re.compile(r"https?://[^\s/]+\.[^\s]+")
_YEAR_RX = re.compile(r"\b(?:19|20)\d{2}\b")
# "Doing less" is not optimizing: fewer epochs/steps, a smaller input, skipped checkpoints or output.
_DO_LESS_RX = re.compile(
    r"\b(?:reduce|reducing|fewer|cut|cutting|skip|skipping|drop|dropping|disable|disabling|remove|removing|shrink|smaller|lower|lowering)\b"
    r"[^.;\n]{0,40}?\b(?:epochs?|iterations?|steps?|timesteps?|dataset|samples|problem size|input size|checkpoints?|"
    r"output files?|outputs?|validation|tolerance|resolution)\b", re.I)


def _add(checks: List[dict], name: str, ok: bool, detail: str, fix: str = "") -> None:
    checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})


def _verdict(checks: List[dict], **extra) -> str:
    if not checks:
        return json.dumps({"status": "inconclusive", "checks": [], "reason": "nothing to check: give at least one input", **extra})
    failed = [c["check"] for c in checks if not c["ok"]]
    return json.dumps({"status": "fail" if failed else "pass", "checks": checks, "failed": failed, **extra}, default=str)


def _parse_json_arg(text: str):
    """JSON text, or the path of a JSON file. Raises ValueError with a readable message."""
    t = (text or "").strip()
    if not t:
        raise ValueError("empty input")
    if t[0] not in "[{\"" and Path(t).is_file():
        t = Path(t).read_text()
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON: {e}") from None


def _rows(data, keys=("rows", "proposals", "accepted", "findings", "papers", "citations", "entries")) -> List[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return data[k]
    return []


def _citation_problem(cit) -> str:
    """'' when the citation is usable, else why it is not."""
    if isinstance(cit, dict):
        if not str(cit.get("title") or "").strip():
            return "citation object has no title"
        if not (cit.get("url") or cit.get("doi") or (cit.get("venue") and cit.get("year"))):
            return "citation object needs url, doi, or venue and year"
        return ""
    c = str(cit or "").strip()
    if not c:
        return "no citation"
    if c.lower().startswith("session:"):
        return "" if len(c) > len("session:") else "session: citation without a run id"
    if c.lower() in _BUILTIN_REFS:
        return ""
    if _URL_RX.search(c) or _DOI_RX.search(c) or _ARXIV_RX.search(c):
        return ""
    if _YEAR_RX.search(c) and len(c) >= 15:
        return ""
    return "not a URL, DOI, arXiv id, built-in reference key or 'Author et al., Venue Year'"


def _level_of(row: dict) -> str:
    lv = str(row.get("level") or row.get("scope") or "").strip()
    if lv.upper() in _LEVELS:
        return lv.upper()
    return _SCOPE_LEVEL.get(lv.lower(), "")


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _row_text(row: dict) -> str:
    return " ".join(str(row.get(k) or "") for k in ("title", "change", "strategy", "description"))

# --------------------------------------------------------------------------- dftracer_diagnose_check
_SEVERITY = {1: "trivial", 2: "low", 3: "medium", 4: "high", 5: "critical"}


def _diagnose_check(diagnosis_file: str, run_id: str, diagnosis_json: str) -> str:
    from dftracer_agents.mcp_tools.tools.session.workspace import _ws
    checks: List[dict] = []
    if diagnosis_json.strip():
        src = "diagnosis_json"
        text = diagnosis_json
    else:
        if diagnosis_file:
            p = Path(diagnosis_file)
        elif run_id:
            p = _ws(run_id) / "diagnosis.json"
        else:
            return _verdict([])
        src = str(p)
        if not p.is_file():
            _add(checks, "diagnosis_exists", False, f"unknown run or no diagnosis: {p} does not exist",
                 "run session_diagnose_bottlenecks for this run (after split/compact), or pass the right run_id")
            return _verdict(checks)
        text = p.read_text()
    try:
        d = json.loads(text)
    except ValueError as e:
        _add(checks, "diagnosis_parse", False, f"{src}: {e}", "re-run session_diagnose_bottlenecks; the file is truncated")
        return _verdict(checks)
    if not isinstance(d, dict):
        _add(checks, "diagnosis_parse", False, f"{src}: not a JSON object", "pass diagnosis.json as written by session_diagnose_bottlenecks")
        return _verdict(checks)
    sc = d.get("severity_counts") or {}
    _add(checks, "severity_counts", isinstance(sc, dict) and set(sc) >= set(_SEVERITY.values()) and all(_num(v) for v in sc.values()),
         f"severity_counts={sc}", "the dfdiagnoser phase did not score: check phases.dfdiagnoser.stderr and re-run")
    bns = d.get("bottlenecks")
    _add(checks, "bottlenecks_list", isinstance(bns, list), f"{len(bns) if isinstance(bns, list) else 'no'} bottleneck(s)",
         "re-run session_diagnose_bottlenecks")
    bns = [b for b in bns if isinstance(b, dict)] if isinstance(bns, list) else []
    bad = [i for i, b in enumerate(bns, 1) if not (b.get("metric") and b.get("view") and isinstance(b.get("score"), int))]
    _add(checks, "bottleneck_fields", not bad, f"rows missing metric/view/integer score: {bad}" if bad else "ok",
         "bottlenecks come from *_scored.json: re-run the diagnosis rather than editing them")
    mism = [i for i, b in enumerate(bns, 1) if isinstance(b.get("score"), int) and b.get("severity") != _SEVERITY.get(b["score"])]
    _add(checks, "severity_matches_score", not mism, f"rows whose severity label disagrees with the score: {mism}" if mism else "ok",
         "severity is 1 trivial, 2 low, 3 medium, 4 high, 5 critical")
    low = [i for i, b in enumerate(bns, 1) if isinstance(b.get("score"), int) and b["score"] < 4]
    _add(checks, "only_high_or_critical", not low, f"rows below high: {low}" if low else "ok", "the ranked list holds score >= 4 only")
    scores = [b.get("score") for b in bns if isinstance(b.get("score"), int)]
    _add(checks, "ranked", scores == sorted(scores, reverse=True), "sorted by score, highest first" if scores == sorted(scores, reverse=True) else "not sorted",
         "rank bottlenecks by score descending")
    ph = d.get("phases") or {}
    if ph:
        ana = (ph.get("dfanalyzer") or {}).get("success")
        _add(checks, "dfanalyzer_phase", ana is True, f"dfanalyzer success={ana}", "fix the analyzer run (index first, generic preset) and diagnose again")
        dg = (ph.get("dfdiagnoser") or {}).get("success")
        _add(checks, "dfdiagnoser_phase", dg is True or bool(bns), f"dfdiagnoser success={dg}",
             "install dfdiagnoser (Python API or CLI) in the session venv and diagnose again")
    cp = d.get("checkpoint_dir")
    if cp:
        views = list(Path(cp).glob("_flat_view_*.parquet")) if Path(cp).is_dir() else []
        _add(checks, "checkpoint_present", bool(views), f"{len(views)} flat view(s) in {cp}",
             "the checkpoint the diagnosis was built from is gone: re-run session_diagnose_bottlenecks")
    return _verdict(checks, source=src, bottlenecks=len(bns))


def register(mcp):
    """Register this capability's tools on *mcp*."""

    @mcp.tool()
    def diagnose(
        checkpoint_dir: str,
        output_dir: Optional[str] = None,
        output_format: str = "json",
        metric_boundaries: Optional[str] = None,
        timeout: int = 300,
    ) -> str:
        """Diagnose I/O bottlenecks from a DFAnalyzer checkpoint directory.

        Loads the ``_flat_view_*.parquet`` files produced by dfanalyzer
        (when run with ``analyzer.checkpoint=True``) and scores every
        metric against severity thresholds defined by DFDiagnoser:

        * ``trivial`` (1) — below 25 % of threshold / baseline
        * ``low``     (2) — 25–50 %
        * ``medium``  (3) — 50–75 %
        * ``high``    (4) — 75–90 %
        * ``critical``(5) — above 90 % of threshold / baseline

        The tool attempts the DFDiagnoser Python API first; if the package
        is not installed it falls back to the ``dfdiagnoser`` CLI binary.

        Typical upstream step::

            dfanalyzer \\
                trace_path=<trace_dir> \\
                "analyzer.checkpoint=True" \\
                "analyzer.checkpoint_dir=<checkpoint_dir>" \\
                "analyzer/preset=posix" \\
                "view_types=[time_range]"

        Args:
            checkpoint_dir: Path to the DFAnalyzer checkpoint directory
                (must contain ``_flat_view_*.parquet`` and
                ``_raw_stats_*.json`` files).
            output_dir: Directory where scored flat views are written.
                Defaults to ``<checkpoint_dir>/scored/``.
            output_format: Format for scored output files.  One of
                ``"json"`` (default), ``"csv"``, or ``"parquet"``.
            metric_boundaries: Optional JSON object string mapping metric
                names to their peak-performance reference values.  Used
                by DFDiagnoser to normalise bandwidth/IOPS metrics against
                hardware limits (e.g. ``'{"bw_mean": 10000000000}'``).
                Defaults to ``None`` (no boundary normalisation).
            timeout: Seconds before the diagnosis subprocess is killed.
                Defaults to ``300``.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"`` or ``"error"``).
                * ``message`` — outcome description.
                * ``checkpoint_dir`` — the directory that was analysed.
                * ``output_dir`` — where scored views were written.
                * ``severity_counts`` — dict mapping severity label to count
                  of (view, metric) pairs at that severity level.
                * ``bottlenecks`` — list of high/critical findings, each
                  with ``view``, ``scope``, ``metric``, ``severity``,
                  ``description``, and ``value`` keys.
                  Sorted by severity score descending, capped at 50 entries.
                * ``raw_stats_summary`` — top-level keys from the
                  ``_raw_stats_*.json`` checkpoint file, if present.
                * ``diagnose_result`` — subprocess/API run result dict.

        Raises:
            Returns ``{"status": "error"}`` when:
                * *checkpoint_dir* does not exist or is empty.
                * No ``_flat_view_*.parquet`` files are found.
                * Both the Python API and CLI fail.
        """
        # ── Validate checkpoint dir ──────────────────────────────────
        cp = Path(checkpoint_dir)
        if not cp.exists():
            return json.dumps({
                "status": "error",
                "message": f"Checkpoint directory not found: {checkpoint_dir}",
            }, indent=2)
        flat_views = list(cp.glob("_flat_view_*.parquet"))
        if not flat_views:
            return json.dumps({
                "status": "error",
                "message": (
                    f"No _flat_view_*.parquet files in {checkpoint_dir}. "
                    "Run dfanalyzer with analyzer.checkpoint=True first."
                ),
            }, indent=2)

        out_dir = output_dir or str(cp / "scored")
        Path(out_dir).mkdir(parents=True, exist_ok=True)

        boundaries = json.loads(metric_boundaries) if metric_boundaries else {}

        # ── Run diagnosis (API → pandas fallback; CLI never supports
        # checkpoint input so it is not a real fallback for this path and
        # would otherwise silently swallow a genuine API exception) ────
        api_result = _diagnose_via_api(checkpoint_dir, out_dir, output_format, boundaries)
        used_api_findings = bool(
            api_result and api_result.get("success") and "findings" in api_result
        )
        if api_result is None:
            # API unavailable (package not installed / method missing) —
            # try direct pandas scoring (primary fallback).
            run_result = _diagnose_via_pandas(checkpoint_dir, out_dir, output_format)
        elif not api_result["success"]:
            # API was attempted and raised — surface its real error
            # instead of masking it behind the CLI's generic
            # "does not support input=checkpoint" message.
            run_result = api_result
        else:
            run_result = api_result

        # ── Parse scored outputs ──────────────────────────────────────
        if used_api_findings:
            # DFDiagnoser's diagnose_checkpoint() findings are motif-based
            # (real trend/severity analysis), not the pandas fallback's
            # naive per-column percentile scoring — parse them directly
            # instead of going through the *_scored.* file format.
            severity_counts, bottlenecks = _extract_bottlenecks_from_findings(
                run_result["findings"]
            )
            scored_views = run_result["findings"]  # for len() below
        else:
            scored_views = _load_scored_views(out_dir)
            severity_counts, bottlenecks = _extract_bottlenecks(scored_views)
        raw_stats = _load_raw_stats(checkpoint_dir)

        total_issues = sum(severity_counts.values())
        critical_high = severity_counts["critical"] + severity_counts["high"]
        msg = (
            f"Diagnosis complete: {total_issues} finding(s)"
            if used_api_findings
            else f"Diagnosis complete: {total_issues} metric observations across "
            f"{len(scored_views)} view(s)."
        )
        msg += f" {critical_high} high/critical issue(s) found."

        if not run_result["success"] and not scored_views:
            return json.dumps({
                "status": "error",
                "message": f"Diagnosis failed: {run_result['stderr']}",
                "checkpoint_dir": checkpoint_dir,
                "diagnose_result": run_result,
            }, indent=2)

        return json.dumps({
            "status": "ok",
            "message": msg,
            "checkpoint_dir": checkpoint_dir,
            "output_dir": out_dir,
            "severity_counts": severity_counts,
            "bottlenecks": bottlenecks[:50],
            "raw_stats_summary": (
                {k: raw_stats[k] for k in list(raw_stats)[:20]}
                if raw_stats else None
            ),
            "diagnose_result": run_result,
        }, indent=2)

    @mcp.tool()
    def session_diagnose_bottlenecks(
        run_id: str,
        analyzer_preset: str = "posix",
        view_types: Optional[str] = "time_range",
        metric_boundaries: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> str:
        """Diagnose I/O bottlenecks by running DFAnalyzer + DFDiagnoser on session traces.

        Two-phase pipeline:

        **Phase 1 — DFAnalyzer checkpoint**
            Runs ``dfanalyzer`` with ``analyzer.checkpoint=True`` on the split
            traces in ``<workspace>/traces_split/``, writing checkpoint files
            (``_flat_view_*.parquet``, ``_raw_stats_*.json``) to
            ``<workspace>/dfanalyzer_checkpoint/``.

        **Phase 2 — DFDiagnoser**
            Loads the checkpoint and scores every metric against severity
            thresholds (trivial → critical).  Scored views are written to
            ``<workspace>/diagnosis/scored/`` and a bottleneck summary is
            saved to ``<workspace>/diagnosis.json``.

        Severity levels (DFDiagnoser convention):
            * ``trivial`` — metric below 25 % of threshold
            * ``low``     — 25–50 %
            * ``medium``  — 50–75 %
            * ``high``    — 75–90 %  ← surfaces as a bottleneck
            * ``critical``— above 90 % ← surfaces as a bottleneck

        Side effects:
            * Creates ``<workspace>/dfanalyzer_checkpoint/``.
            * Creates ``<workspace>/diagnosis/scored/``.
            * Writes ``<workspace>/diagnosis.json`` with the bottleneck summary.
            * Persists ``{"step": "bottlenecks_diagnosed", ...}`` to ``session.json``.
            * Writes an artifact log at step 15.

        Args:
            run_id: Session identifier returned by ``session_create``.
            analyzer_preset: DFAnalyzer preset.  ``"posix"`` covers POSIX
                file I/O; ``"dlio"`` covers deep-learning I/O workloads.
                Defaults to ``"posix"``.
            view_types: Comma-separated DFAnalyzer view type(s).
                Defaults to ``"time_range"``.
            metric_boundaries: Optional JSON object string mapping metric names
                to hardware peak values for bandwidth/IOPS normalisation.
                Defaults to ``None``.
            timeout: Seconds before each subprocess phase is killed.
                Defaults to ``None`` (no timeout) — large (26M+ event)
                trace sets can legitimately take much longer to
                analyze/diagnose than smaller ones, and a fixed bound
                would kill a legitimate long-running diagnosis. Pass an
                explicit value only if you deliberately want a bound.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"`` or ``"error"``).
                * ``message`` — outcome description.
                * ``diagnosis_file`` — path to ``diagnosis.json``.
                * ``checkpoint_dir`` — dfanalyzer checkpoint directory.
                * ``severity_counts`` — per-severity metric observation counts.
                * ``bottlenecks`` — list of high/critical findings (up to 50).
                * ``phases`` — subprocess result dicts for debugging.

        Raises:
            Returns ``{"status": "error"}`` when:
                * ``traces_split/`` does not exist (run ``session_split_traces`` first).
                * dfanalyzer fails to produce checkpoint files.
                * DFDiagnoser is not installed and no CLI binary is found.
        """
        return _session_diagnose_bottlenecks_impl(
            run_id=run_id,
            analyzer_preset=analyzer_preset,
            view_types=view_types,
            metric_boundaries=metric_boundaries,
            timeout=timeout,
        )


    @mcp.tool()
    def dftracer_diagnose_check(diagnosis_file: str = "", run_id: str = "", diagnosis_json: str = "") -> str:
        """Deterministically validate a bottleneck diagnosis (diagnosis.json from session_diagnose_bottlenecks).

        Use after diagnosing and before optimizing. Give diagnosis_json (the JSON text), or diagnosis_file (a path),
        or run_id (reads <workspace>/<run_id>/diagnosis.json; an unknown run fails with a clear message).
        Checks: it parses; severity_counts has the five labels; every bottleneck has metric, view and an integer
        score, its severity label matches the score, only high/critical (score >= 4) rows are listed, ranked
        highest first; the dfanalyzer and dfdiagnoser phases succeeded; the checkpoint it came from still exists.

        Returns JSON with keys: status ("pass" | "fail" | "inconclusive" when nothing was given), checks (check, ok,
        detail, fix), failed, source, bottlenecks.
        """
        return _diagnose_check(diagnosis_file, run_id, diagnosis_json)
