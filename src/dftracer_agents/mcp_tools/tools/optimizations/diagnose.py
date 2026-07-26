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


def _session_diagnose_bottlenecks_impl(
    run_id: str,
    analyzer_preset: str = "posix",
    view_types: Optional[str] = "time_range",
    metric_boundaries: Optional[str] = None,
    timeout: Optional[int] = None,
    traces_dir: Optional[str] = None,
) -> str:
    """Implementation of session_diagnose_bottlenecks (callable without MCP).

    Shared between the MCP tool and session_optimization_iteration.

    Args:
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
    timeout: Optional[int],
    traces_dir: Optional[str],
) -> str:
    traces_split = Path(traces_dir) if traces_dir else (ws / "traces_split")
    if not traces_split.exists():
        return _err(
            f"{traces_split} not found — run session_split_traces first",
            run_id=run_id,
        )

    checkpoint_dir = ws / "dfanalyzer_checkpoint"
    diagnosis_dir  = ws / "diagnosis"
    scored_dir     = diagnosis_dir / "scored"
    checkpoint_dir.mkdir(exist_ok=True)
    diagnosis_dir.mkdir(exist_ok=True)
    scored_dir.mkdir(exist_ok=True)

    phases: Dict[str, Any] = {}

    # ── Phase 1: dfanalyzer with checkpoint ──────────────────────────
    vt_list = [v.strip() for v in (view_types or "time_range").split(",") if v.strip()]
    vt_str  = "[" + ",".join(vt_list) + "]"
    dfanalyzer_cmd = [
        "dfanalyzer",
        f"trace_path={traces_split}",
        "analyzer.checkpoint=True",
        f"analyzer.checkpoint_dir={checkpoint_dir}",
        f"analyzer/preset={analyzer_preset}",
        f"view_types={vt_str}",
    ]
    ana_r = _run(dfanalyzer_cmd, timeout=timeout)
    phases["dfanalyzer"] = ana_r
    if not ana_r["success"]:
        return _err(
            f"dfanalyzer failed (exit {ana_r['returncode']})",
            phases=phases,
            hint="Ensure dfanalyzer is installed: pip install dfanalyzer-utils",
            stderr=ana_r["stderr"],
        )

    flat_views = list(checkpoint_dir.glob("_flat_view_*.parquet"))
    if not flat_views:
        return _err(
            f"dfanalyzer ran but produced no _flat_view_*.parquet in {checkpoint_dir}",
            phases=phases,
            dfanalyzer_stdout=ana_r["stdout"],
        )

    # ── Phase 2: dfdiagnoser ─────────────────────────────────────────
    # Try Python API first, fall back to CLI.
    boundaries = json.loads(metric_boundaries) if metric_boundaries else {}
    diag_r: Optional[Dict[str, Any]] = None
    try:
        from dfdiagnoser.diagnoser import Diagnoser   # type: ignore
        from dfdiagnoser.output import FileOutput     # type: ignore
        diagnoser = Diagnoser()
        result = diagnoser.diagnose_checkpoint(
            checkpoint_dir=str(checkpoint_dir),
            metric_boundaries=boundaries,
        )
        FileOutput(output_dir=str(scored_dir), output_format="json").handle_result(result)
        diag_r = {
            "returncode": 0,
            "stdout": f"Scored {len(result.scored_flat_views)} view(s) via Python API",
            "stderr": "",
            "success": True,
        }
    except ImportError:
        # CLI fallback
        cli_cmd = [
            "dfdiagnoser",
            "input=checkpoint",
            f"input.checkpoint_dir={checkpoint_dir}",
            "output=file",
            f"output.output_dir={scored_dir}",
            "output.output_format=json",
        ]
        diag_r = _run(cli_cmd, timeout=timeout)
        if not diag_r["success"] and "not found" in diag_r.get("stderr", "").lower():
            diag_r["stderr"] += " — install with: pip install dfdiagnoser"
    except Exception as exc:
        diag_r = {"returncode": -1, "stdout": "", "stderr": str(exc), "success": False}

    phases["dfdiagnoser"] = diag_r

    # ── Parse scored outputs and build bottleneck summary ─────────────
    severity_counts: Dict[str, int] = {
        "critical": 0, "high": 0, "medium": 0, "low": 0, "trivial": 0
    }
    score_labels = {1: "trivial", 2: "low", 3: "medium", 4: "high", 5: "critical"}
    bottlenecks: List[Dict[str, Any]] = []

    for scored_path in sorted(scored_dir.glob("*_scored.json")):
        try:
            with open(scored_path) as f:
                rows = json.load(f)
            view_name = scored_path.stem
            for row_key, row in (rows.items() if isinstance(rows, dict) else []):
                for col, val in row.items():
                    if not col.endswith("_score") or val is None:
                        continue
                    metric = col[:-6]  # strip "_score"
                    try:
                        score = int(val)
                    except (TypeError, ValueError):
                        continue
                    label = score_labels.get(score, "unknown")
                    if label in severity_counts:
                        severity_counts[label] += 1
                    if score >= 4:
                        bottlenecks.append({
                            "view":        view_name,
                            "scope":       str(row_key),
                            "metric":      metric,
                            "score":       score,
                            "severity":    label,
                            "value":       row.get(metric),
                        })
        except Exception:
            pass

    bottlenecks.sort(key=lambda x: x["score"], reverse=True)

    # ── Load raw stats for context ────────────────────────────────────
    raw_stats: Optional[Dict[str, Any]] = None
    for p in checkpoint_dir.glob("_raw_stats_*.json"):
        try:
            with open(p) as f:
                raw_stats = json.load(f)
            break
        except Exception:
            pass

    # ── Persist summary ───────────────────────────────────────────────
    total_issues = sum(severity_counts.values())
    critical_high = severity_counts["critical"] + severity_counts["high"]
    summary = {
        "run_id":          run_id,
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
        "checkpoint_dir":   str(checkpoint_dir),
        "severity_counts":  severity_counts,
    })
    _write_artifact_log(ws, 15, "session_diagnose_bottlenecks", {
        "total_metrics_scored": total_issues,
        "high_critical":        critical_high,
        "severity_counts":      severity_counts,
    }, run_id)

    msg = (
        f"Bottleneck diagnosis complete: {total_issues} metric observations, "
        f"{critical_high} high/critical issue(s) identified."
    )
    if not diag_r.get("success") and not bottlenecks:
        msg = f"DFDiagnoser did not run successfully: {diag_r.get('stderr', '')}"

    return _ok(
        msg,
        diagnosis_file=str(diagnosis_file),
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
