"""Optimization diagnose tools — session_diagnose_bottlenecks and session_search_optimization_papers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from ..session.workspace import (
    _ws, _load_state, _save_state, _write_artifact_log, _ok, _err, _run, _workspaces_root,
)
from .strategies import _fetch_arxiv_papers, _METRIC_SYNONYM_PAIRS, _GENERAL_FALLBACK_QUERIES


def _run_dfanalyzer_facts(
    traces_split: Path,
    facts_dir: Path,
    checkpoint_dir: Optional[Path],
    analyzer_preset: str,
    vt_str: str,
    timeout: int,
) -> Dict[str, Any]:
    """Run dfanalyzer producing an ``output=file`` facts bundle (``facts.jsonl`` +
    ``raw_stats.json``) — the format ``dfdiagnoser input=file`` / ``diagnose_file()``
    consumes. Optionally also writes an ``analyzer.checkpoint`` cache (a distinct,
    independent mechanism used only for incremental recompute, not by the diagnoser).

    ``facts.enabled=True`` is required — it defaults to False, and with it
    unset ``result.analysis_facts`` is empty so ``FileOutput`` silently skips
    writing ``facts.jsonl`` (dfanalyzer itself still reports success).
    ``facts.eval_mode=metric`` selects the rule-free ``MetricFactBuilder``
    (automatic ``*_ops_slope`` detection, no thresholds to tune) instead of the
    default ``eval_mode=rule``, which requires a preset-specific
    ``fact_rules/<preset>.yaml`` file that most presets (posix, generic) don't
    ship — metric mode works for every preset uniformly.
    """
    cmd = [
        "dfanalyzer",
        f"trace_path={traces_split}",
        f"analyzer/preset={analyzer_preset}",
        f"view_types={vt_str}",
        "output=file",
        f"output.path={facts_dir}",
        "facts.enabled=True",
        "facts.eval_mode=metric",
    ]
    if checkpoint_dir is not None:
        cmd += ["analyzer.checkpoint=True", f"analyzer.checkpoint_dir={checkpoint_dir}"]
    return _run(cmd, timeout=timeout)


def _run_dfdiagnoser_findings(
    facts_dir: Path,
    findings_dir: Path,
    timeout: int,
    metric_boundaries: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Run dfdiagnoser's offline file-bundle diagnosis: ``diagnose_file(facts_dir)``
    via the Python API (preferred) or ``dfdiagnoser input=file`` CLI fallback.
    Writes ``findings.jsonl`` to *findings_dir* — scored, classified findings
    (severity, severity_score, confidence, motif, recommendation_bundle) that
    the analyzer's own fact pipeline already computed; the diagnoser is purely
    a fact consumer/replayer, not a re-scorer. Superseded ``diagnose_checkpoint``
    (removed upstream) and the old ``*_scored.json`` percentile-column format.

    ``metric_boundaries`` is only honored via the Python API — upstream's CLI
    entrypoint does not expose it as a Hydra-configurable field, so the CLI
    fallback runs without boundary normalization (same limitation the old
    checkpoint-based CLI path had).
    """
    try:
        from dfdiagnoser.diagnoser import Diagnoser   # type: ignore
        from dfdiagnoser.output import FileOutput     # type: ignore
        diagnoser = Diagnoser()
        result = diagnoser.diagnose_file(str(facts_dir), metric_boundaries=metric_boundaries or {})
        FileOutput(output_dir=str(findings_dir), output_format="json").handle_result(result)
        return {
            "returncode": 0,
            "stdout": f"Diagnosed {len(result.findings)} finding(s) via Python API (diagnose_file)",
            "stderr": "",
            "success": True,
        }
    except ImportError:
        cli_cmd = [
            "dfdiagnoser",
            "input=file",
            f"input.path={facts_dir}",
            "output=file",
            f"output.output_dir={findings_dir}",
        ]
        r = _run(cli_cmd, timeout=timeout)
        if not r["success"] and "not found" in r.get("stderr", "").lower():
            r["stderr"] += " — install with: pip install dfdiagnoser"
        return r
    except Exception as exc:
        return {"returncode": -1, "stdout": "", "stderr": str(exc), "success": False}


def _parse_findings_jsonl(findings_dir: Path) -> "tuple[Dict[str, int], List[Dict[str, Any]]]":
    """Parse ``findings.jsonl`` (one ``DiagnosisFinding.to_wire_dict()`` record per
    line) into severity counts and a ranked bottleneck list. Replaces the old
    ``*_scored.json`` / ``<metric>_score`` column parsing — findings are already
    classified (severity, severity_score, confidence, motif) by the analyzer's
    fact pipeline, so no local re-scoring is needed.
    """
    severity_counts: Dict[str, int] = {
        "critical": 0, "high": 0, "medium": 0, "low": 0, "trivial": 0
    }
    bottlenecks: List[Dict[str, Any]] = []
    findings_path = findings_dir / "findings.jsonl"
    if not findings_path.exists():
        return severity_counts, bottlenecks

    with open(findings_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            sev = str(rec.get("severity", "unknown")).lower()
            if sev in severity_counts:
                severity_counts[sev] += 1
            score = rec.get("severity_score")
            is_bottleneck = sev in ("high", "critical") or (
                isinstance(score, (int, float)) and score >= 0.75
            )
            if is_bottleneck:
                bottlenecks.append({
                    "finding_type":          rec.get("finding_type"),
                    "scope":                 rec.get("scope"),
                    "layer":                 rec.get("layer"),
                    "motif":                 rec.get("motif"),
                    "severity":              sev,
                    "severity_score":        score,
                    "confidence":            rec.get("confidence"),
                    "summary":               rec.get("summary"),
                    "recommendation_bundle": rec.get("recommendation_bundle"),
                    "opportunity_tags":      rec.get("opportunity_tags"),
                    "key_metrics":           rec.get("key_metrics"),
                    "view_type":             rec.get("view_type"),
                })

    bottlenecks.sort(key=lambda x: (x.get("severity_score") or 0), reverse=True)
    return severity_counts, bottlenecks


def _session_diagnose_bottlenecks_impl(
    run_id: str,
    analyzer_preset: str = "posix",
    view_types: Optional[str] = "time_range",
    metric_boundaries: Optional[str] = None,
    timeout: int = 600,
    traces_dir: Optional[str] = None,
) -> str:
    """Implementation of session_diagnose_bottlenecks (callable without MCP).

    Shared between the MCP tool and session_optimization_iteration.

    Args:
        analyzer_preset: dfanalyzer preset name(s) (``analyzer/preset=<name>``).
            ``"posix"`` (default) and ``"dlio"`` are hand-tuned presets; use
            ``"generic"`` for traces whose ``cat`` values don't fit either
            (custom app-level annotation categories, mixed/unknown workloads)
            — it auto-discovers distinct ``cat`` values at runtime and builds
            one layer per category rather than requiring a hand-picked preset.
            **Accepts a comma-separated list** (e.g. ``"dlio,generic"``) to run
            the full analyze+diagnose pipeline once per preset and merge the
            results (each bottleneck tagged with its source ``preset``) — this
            is how a DL workload gets both dlio's hand-tuned layers and
            generic's auto-discovered per-category coverage in one call, and
            how any workload can widen coverage instead of committing to a
            single hand-picked preset. See ``_detect_analyzer_presets`` in
            ``session/detection.py`` for the automatic per-workload default.
        traces_dir: Optional explicit path to the split/compact trace
            directory to analyze. Defaults to ``<ws>/traces_split/`` (the
            standalone-tool convention). ``session_optimization_iteration``
            passes its own per-iteration ``<ws>/opt{N}/traces/compact/``
            directory here — without this, every iteration would silently
            re-analyze whatever is sitting in the shared ``traces_split/``
            directory instead of that iteration's own freshly-collected data.
    """
    ws = _ws(run_id)
    try:
        return _session_diagnose_bottlenecks_impl_inner(
            run_id, ws, analyzer_preset, view_types, metric_boundaries, timeout, traces_dir,
        )
    except Exception as _exc:
        import traceback as _tb
        crash_file = ws / "diagnosis_crash.json"
        crash_file.write_text(json.dumps({
            "run_id": run_id,
            "traces_dir": traces_dir,
            "exception": str(_exc),
            "traceback": _tb.format_exc(),
        }, indent=2))
        return _err(
            f"_session_diagnose_bottlenecks_impl raised an unexpected exception: {_exc}",
            run_id=run_id,
            crash_file=str(crash_file),
            traceback=_tb.format_exc(),
        )


def _session_diagnose_bottlenecks_impl_inner(
    run_id: str,
    ws: Path,
    analyzer_preset: str,
    view_types: Optional[str],
    metric_boundaries: Optional[str],
    timeout: int,
    traces_dir: Optional[str],
) -> str:
    traces_split = Path(traces_dir) if traces_dir else (ws / "traces_split")
    if not traces_split.exists():
        return _err(
            f"{traces_split} not found — run session_split_traces first",
            run_id=run_id,
        )

    checkpoint_dir = ws / "dfanalyzer_checkpoint"   # incremental-recompute cache only
    facts_dir      = ws / "dfanalyzer_facts"         # output=file bundle: facts.jsonl, raw_stats.json
    diagnosis_dir  = ws / "diagnosis"
    findings_dir   = diagnosis_dir / "findings"
    checkpoint_dir.mkdir(exist_ok=True)
    facts_dir.mkdir(exist_ok=True)
    diagnosis_dir.mkdir(exist_ok=True)
    findings_dir.mkdir(exist_ok=True)

    # Multi-preset support: comma-separated analyzer_preset (e.g. "dlio,generic")
    # runs the full dfanalyzer+dfdiagnoser pipeline once per preset, into its own
    # subdirectory, then merges severity counts and bottleneck lists (each tagged
    # with its source preset). This is how DL workloads get both dlio's hand-tuned
    # layers AND generic's auto-discovered per-category layers in one diagnosis,
    # and how any workload can widen coverage without hand-picking a single preset.
    presets = [p.strip() for p in analyzer_preset.split(",") if p.strip()] or ["posix"]

    vt_list = [v.strip() for v in (view_types or "time_range").split(",") if v.strip()]
    vt_str  = "[" + ",".join(vt_list) + "]"
    boundaries = json.loads(metric_boundaries) if metric_boundaries else {}

    phases: Dict[str, Any] = {}
    severity_counts: Dict[str, int] = {
        "critical": 0, "high": 0, "medium": 0, "low": 0, "trivial": 0
    }
    bottlenecks: List[Dict[str, Any]] = []
    raw_stats: Optional[Dict[str, Any]] = None
    any_facts_produced = False

    for preset in presets:
        preset_facts_dir      = facts_dir / preset
        preset_checkpoint_dir = checkpoint_dir / preset
        preset_findings_dir   = findings_dir / preset
        preset_facts_dir.mkdir(exist_ok=True)
        preset_checkpoint_dir.mkdir(exist_ok=True)
        preset_findings_dir.mkdir(exist_ok=True)

        # ── Phase 1: dfanalyzer (output=file facts bundle + checkpoint cache) ──
        ana_r = _run_dfanalyzer_facts(
            traces_split, preset_facts_dir, preset_checkpoint_dir, preset, vt_str, timeout,
        )
        phases[f"dfanalyzer[{preset}]"] = ana_r
        if not ana_r["success"]:
            phases[f"dfanalyzer[{preset}]_error"] = (
                f"dfanalyzer failed (exit {ana_r['returncode']})"
            )
            continue
        if not (preset_facts_dir / "facts.jsonl").exists():
            phases[f"dfanalyzer[{preset}]_error"] = (
                f"dfanalyzer ran but produced no facts.jsonl in {preset_facts_dir}"
            )
            continue
        any_facts_produced = True

        # ── Phase 2: dfdiagnoser — diagnose_file() replays facts.jsonl into ────
        # classified findings (severity/motif/confidence); scoring lives in the
        # analyzer's fact pipeline, the diagnoser is a pure fact consumer here.
        diag_r = _run_dfdiagnoser_findings(preset_facts_dir, preset_findings_dir, timeout, boundaries)
        phases[f"dfdiagnoser[{preset}]"] = diag_r

        preset_severity_counts, preset_bottlenecks = _parse_findings_jsonl(preset_findings_dir)
        for label, count in preset_severity_counts.items():
            severity_counts[label] = severity_counts.get(label, 0) + count
        for b in preset_bottlenecks:
            b["preset"] = preset
        bottlenecks.extend(preset_bottlenecks)

        if raw_stats is None:
            raw_stats_path = preset_facts_dir / "raw_stats.json"
            if raw_stats_path.exists():
                try:
                    with open(raw_stats_path) as f:
                        raw_stats = json.load(f)
                except Exception:
                    pass

    if not any_facts_produced:
        return _err(
            f"dfanalyzer failed to produce facts.jsonl for any preset ({presets})",
            phases=phases,
            hint="Ensure dfanalyzer is installed: pip install dfanalyzer-utils",
        )

    bottlenecks.sort(key=lambda x: (x.get("severity_score") or 0), reverse=True)
    diag_r = {"success": any(v.get("success") for k, v in phases.items() if k.startswith("dfdiagnoser["))}

    # ── Persist summary ───────────────────────────────────────────────
    total_issues = sum(severity_counts.values())
    critical_high = severity_counts["critical"] + severity_counts["high"]
    summary = {
        "run_id":          run_id,
        "presets":         presets,
        "facts_dir":       str(facts_dir),
        "checkpoint_dir":  str(checkpoint_dir),
        "diagnosis_dir":   str(diagnosis_dir),
        "severity_counts": severity_counts,
        "bottlenecks":     bottlenecks[:50],
        "raw_stats":       raw_stats,
        "phases":          phases,
    }
    diagnosis_file = ws / "diagnosis.json"
    diagnosis_file.write_text(json.dumps(summary, indent=2))

    _save_state(run_id, {
        "step":             "bottlenecks_diagnosed",
        "diagnosis_file":   str(diagnosis_file),
        "facts_dir":        str(facts_dir),
        "checkpoint_dir":   str(checkpoint_dir),
        "severity_counts":  severity_counts,
    })
    _write_artifact_log(ws, 15, "session_diagnose_bottlenecks", {
        "total_findings":  total_issues,
        "high_critical":   critical_high,
        "severity_counts": severity_counts,
    }, run_id)

    msg = (
        f"Bottleneck diagnosis complete: {total_issues} finding(s), "
        f"{critical_high} high/critical issue(s) identified."
    )
    if not diag_r.get("success") and not bottlenecks:
        msg = f"DFDiagnoser did not run successfully: {diag_r.get('stderr', '')}"

    return _ok(
        msg,
        diagnosis_file=str(diagnosis_file),
        facts_dir=str(facts_dir),
        checkpoint_dir=str(checkpoint_dir),
        severity_counts=severity_counts,
        bottlenecks=bottlenecks[:50],
        phases=phases,
    )


def register_diagnose_tools(mcp: FastMCP) -> None:
    """Register session_search_optimization_papers onto *mcp*.

    Note: session_diagnose_bottlenecks lives in dfdiagnoser_service.py
    (DFDiagnoserService.session_subservice) as it belongs with the diagnoser service.
    """

    @mcp.tool()
    def session_search_optimization_papers(
        run_id: str,
        max_results_per_topic: int = 3,
        extra_query: Optional[str] = None,
    ) -> str:
        """Search arXiv for optimization papers relevant to the diagnosed bottlenecks.

        Reads ``<workspace>/diagnosis.json`` (produced by
        ``session_diagnose_bottlenecks``) and maps each high/critical bottleneck
        metric to a targeted arXiv search query.  Results are saved as
        ``<workspace>/optimization_papers.json`` and returned as a structured
        summary for the agent to interpret.

        Metric → query mapping examples:

        * ``small_io``   → "small I/O aggregation buffering optimization HPC"
        * ``rand``       → "random access sequential I/O prefetching optimization"
        * ``read_time``  → "parallel I/O read throughput optimization filesystem"
        * ``write_time`` → "parallel I/O write throughput checkpoint optimization"
        * ``metadata``   → "metadata operation overhead reduction parallel filesystem"

        Args:
            run_id: Session identifier returned by ``session_create``.
            max_results_per_topic: Papers to fetch per unique bottleneck topic
                (1-10, default 3).
            extra_query: Optional additional search terms appended to every query
                (e.g. the application name or storage system name).

        Returns:
            JSON with keys:

            * ``status``         — ``"ok"`` or ``"error"``.
            * ``topics_searched``— list of search queries issued.
            * ``papers``         — flat list of unique papers (deduplicated by title),
              each with ``title``, ``authors``, ``published``, ``abstract`` (truncated),
              ``url``, and ``topic``.
            * ``papers_file``    — path to the saved ``optimization_papers.json``.
        """
        ws = _ws(run_id)
        diagnosis_file = ws / "diagnosis.json"
        if not diagnosis_file.exists():
            return _err(
                "diagnosis.json not found — run session_diagnose_bottlenecks first",
                run_id=run_id,
            )

        try:
            diagnosis = json.loads(diagnosis_file.read_text())
        except Exception as exc:
            return _err(f"Could not read diagnosis.json: {exc}", run_id=run_id)

        bottlenecks: List[Dict[str, Any]] = diagnosis.get("bottlenecks", [])

        # Map metric name fragments to human-readable search queries
        _METRIC_QUERIES: Dict[str, str] = {
            "small_io":        "small I/O aggregation buffering optimization HPC parallel filesystem",
            "small_read":      "small read aggregation optimization parallel I/O",
            "small_write":     "small write buffering optimization parallel I/O",
            "rand":            "random I/O access pattern optimization sequential prefetching HPC",
            "seq":             "sequential I/O access pattern fragmentation optimization",
            "read_time":       "parallel I/O read throughput optimization high performance computing",
            "write_time":      "parallel I/O write throughput checkpoint optimization",
            "metadata":        "metadata operation overhead reduction parallel filesystem POSIX",
            "fetch_pressure":  "data loader prefetching pipeline deep learning I/O optimization",
            "epoch_straggler": "stragglers load imbalance distributed training I/O optimization",
            "checkpoint":      "checkpoint I/O optimization deep learning distributed training",
            "intensity":       "I/O intensity compute I/O overlap optimization",
            "imbalance":       "I/O load imbalance optimization distributed HPC",
            "bw":              "bandwidth utilization optimization parallel I/O filesystem",
            "fs_bw":           "parallel filesystem bandwidth utilization storage system throughput HPC",
            "comm":            "MPI collective communication optimization overlap HPC",
            "mem_bw":          "memory bandwidth optimization NUMA cache-aware HPC",
            "compute":         "compute kernel vectorization roofline optimization HPC",
        }

        # Collect unique topics from high/critical bottlenecks
        seen_topics: Dict[str, str] = {}  # query → representative metric name
        for bn in bottlenecks:
            metric = bn.get("metric", "")
            for fragment, query in _METRIC_QUERIES.items():
                if fragment in metric and query not in seen_topics:
                    seen_topics[query] = metric
                    break

        # If no bottlenecks mapped, fall back to a general I/O performance query
        if not seen_topics:
            seen_topics["I/O performance optimization parallel filesystem HPC"] = "general"

        if extra_query:
            seen_topics = {f"{q} {extra_query}": m for q, m in seen_topics.items()}

        # Search arXiv for each topic (synchronous wrapper around async HTTP)
        try:
            import httpx as _httpx  # noqa: F401 — presence check
            import xml.etree.ElementTree as _ET
            import urllib.parse

            _ARXIV = "https://export.arxiv.org/api/query"
            _NS    = {"atom": "http://www.w3.org/2005/Atom",
                      "arxiv": "http://arxiv.org/schemas/atom"}

            def _fetch_arxiv(query: str, n: int) -> List[Dict[str, Any]]:
                params = {
                    "search_query": f"all:{query}",
                    "max_results":  n,
                    "sortBy":       "relevance",
                    "sortOrder":    "descending",
                }
                qs = urllib.parse.urlencode(params)
                url = f"{_ARXIV}?{qs}"
                r = _run(["curl", "-s", "--max-time", "30", url], timeout=45)
                if not r["success"] or not r["stdout"]:
                    return []
                try:
                    root = _ET.fromstring(r["stdout"])
                    papers = []
                    for entry in root.findall("atom:entry", _NS):
                        def _t(tag):
                            el = entry.find(tag, _NS)
                            return el.text.strip() if el is not None and el.text else ""
                        arxiv_id = _t("atom:id").split("/abs/")[-1]
                        authors  = [
                            a.find("atom:name", _NS).text.strip()
                            for a in entry.findall("atom:author", _NS)
                            if a.find("atom:name", _NS) is not None
                        ]
                        papers.append({
                            "title":     _t("atom:title").replace("\n", " "),
                            "authors":   authors,
                            "published": _t("atom:published")[:10],
                            "abstract":  _t("atom:summary").replace("\n", " ")[:400],
                            "url":       f"https://arxiv.org/abs/{arxiv_id}",
                            "pdf_url":   f"https://arxiv.org/pdf/{arxiv_id}",
                        })
                    return papers
                except Exception:
                    return []

            max_results_per_topic = max(1, min(10, max_results_per_topic))
            all_papers: List[Dict[str, Any]] = []
            seen_titles: set = set()
            topics_searched: List[str] = []

            for query, metric in seen_topics.items():
                topics_searched.append(query)
                for p in _fetch_arxiv(query, max_results_per_topic):
                    title_key = p["title"].lower()[:80]
                    if title_key not in seen_titles:
                        seen_titles.add(title_key)
                        all_papers.append({**p, "topic": metric})

        except Exception as exc:
            return _err(f"Paper search failed: {exc}", run_id=run_id)

        result = {
            "run_id":          run_id,
            "topics_searched": topics_searched,
            "papers":          all_papers,
        }
        papers_file = ws / "optimization_papers.json"
        papers_file.write_text(json.dumps(result, indent=2))
        result["papers_file"] = str(papers_file)

        _write_artifact_log(ws, 16, "session_search_optimization_papers", {
            "topics":       len(topics_searched),
            "papers_found": len(all_papers),
        }, run_id)

        return _ok(
            f"Found {len(all_papers)} unique optimization papers across "
            f"{len(topics_searched)} bottleneck topic(s).",
            papers_file=str(papers_file),
            topics_searched=topics_searched,
            papers=all_papers,
        )
