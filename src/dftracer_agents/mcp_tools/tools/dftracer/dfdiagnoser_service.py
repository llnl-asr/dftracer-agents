"""DFDiagnoser MCP service — I/O bottleneck diagnosis from DFAnalyzer facts bundles.

This module exposes the DFDiagnoser library as an MCP tool so that AI agents can
identify I/O bottlenecks in dftracer traces without constructing shell commands
manually.

Background
----------
DFDiagnoser is a pure *fact consumer*: DFAnalyzer's own fact pipeline computes
scored, classified findings (severity, severity_score, confidence, motif,
recommendation_bundle) and writes them as ``facts.jsonl`` when run with
``output=file output.path=<facts_dir>``. DFDiagnoser's ``diagnose_file()``
replays that bundle into ``DiagnosisFinding`` objects and (via ``FileOutput``)
writes them back out as ``findings.jsonl``. There is no ``diagnose_checkpoint``
method — a raw ``analyzer.checkpoint`` directory (``_flat_view_*.parquet``,
used for incremental recompute caching) is a distinct mechanism and is not
diagnosable via the real DFDiagnoser API/CLI.

The primary (facts_dir) path:

1. Runs (or expects already-run) dfanalyzer with ``output=file`` to produce
   ``<facts_dir>/facts.jsonl``.
2. Calls ``Diagnoser().diagnose_file(facts_dir)`` (Python API) or
   ``dfdiagnoser input=file input.path=<facts_dir>`` (CLI fallback).
3. Writes ``findings.jsonl`` (one classified ``DiagnosisFinding`` per line) to
   ``<output_dir>/``.
4. Surfaces high/critical-severity findings as a structured bottleneck summary.

A LEGACY ``checkpoint_dir`` path remains for when only a raw checkpoint exists:
it scores every numeric column with a local percentile heuristic (not real
DFDiagnoser motif/trend classification) since the real diagnoser has no
checkpoint-parquet consumer.

Tools exposed
-------------
* ``diagnose`` — run dfdiagnoser on a DFAnalyzer ``output=file`` facts bundle
  (preferred) or, legacy, a raw checkpoint directory.

Typical pipeline order
----------------------
::

    dfanalyzer trace_path=<traces> analyzer/preset=posix \\
        output=file output.path=<facts_dir>
        → facts_dir/  (facts.jsonl, raw_stats.json)

    diagnose(facts_dir=facts_dir, output_dir=output_dir)
        → findings.jsonl  +  bottleneck summary JSON

References
----------
* https://github.com/llnl/dfdiagnoser
* https://github.com/llnl/dfanalyzer
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastmcp import FastMCP

from ...mcp_service_factory import MCPService, MCPServiceFactory
from ..optimizations.diagnose import (
    _session_diagnose_bottlenecks_impl,
    _run_dfdiagnoser_findings,
    _parse_findings_jsonl,
)

# Score integer → human label (1-indexed, matching dfdiagnoser.scoring.SCORE_NAMES)
_SCORE_LABELS = {1: "trivial", 2: "low", 3: "medium", 4: "high", 5: "critical"}

# Human-readable descriptions for metric suffixes found in dfanalyzer flat views
_METRIC_DESCRIPTIONS: Dict[str, str] = {
    "read_time_pct":            "fraction of wall time spent in read operations",
    "write_time_pct":           "fraction of wall time spent in write operations",
    "metadata_time_pct":        "fraction of wall time spent in metadata operations",
    "metadata_time_frac_parent":"metadata operations as fraction of parent I/O time",
    "small_io_pct":             "fraction of I/O operations that are small (<4 KiB)",
    "small_read_pct":           "fraction of read operations that are small",
    "small_write_pct":          "fraction of write operations that are small",
    "rand_pct":                 "fraction of random (non-sequential) accesses",
    "seq_pct":                  "fraction of sequential accesses (low score = fragmented)",
    "read_size_mean":           "mean read request size",
    "write_size_mean":          "mean write request size",
    "read_bw_mean":             "mean read bandwidth",
    "write_bw_mean":            "mean write bandwidth",
    "read_time_frac_parent":    "read time as fraction of parent I/O time",
    "write_time_frac_parent":   "write time as fraction of parent I/O time",
    "operation_imbalance_ratio":"imbalance ratio between read and write operation counts",
    "size_imbalance_ratio":     "imbalance ratio between read and write data sizes",
    "fetch_pressure":           "data-fetch pipeline pressure (high = reader starving compute)",
    "epoch_straggler":          "straggler epoch latency (high = one epoch much slower than others)",
    "checkpoint_tail_skew":     "tail skew in checkpoint write latency",
    "intensity_mean":           "I/O intensity (bytes/sec relative to compute time)",
}


def _describe_metric(metric: str) -> str:
    """Return a human-readable description for a dfanalyzer metric name."""
    for suffix, desc in _METRIC_DESCRIPTIONS.items():
        if metric.endswith(suffix):
            return desc
    return metric.replace("_", " ")


def _run_cli(cmd: List[str], timeout: int = 300) -> Dict[str, Any]:
    """Run a subprocess and return a normalised result dict."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "returncode": r.returncode,
            "stdout": r.stdout.strip(),
            "stderr": r.stderr.strip(),
            "success": r.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": "Command timed out", "success": False}
    except FileNotFoundError as exc:
        return {"returncode": -1, "stdout": "", "stderr": str(exc), "success": False}
    except Exception as exc:
        return {"returncode": -1, "stdout": "", "stderr": str(exc), "success": False}


## NOTE: The real DFDiagnoser Python API / CLI (`diagnose_file` /
## `dfdiagnoser input=file`) is used via the shared `_run_dfdiagnoser_findings`
## helper (imported from `optimizations/diagnose.py`) for the `facts_dir` path
## above. `_diagnose_via_pandas` below remains the only path for the LEGACY
## `checkpoint_dir` input, since a raw `analyzer.checkpoint` directory
## (`_flat_view_*.parquet`) has no real-DFDiagnoser consumer — `diagnose_file`
## requires an `output=file` bundle (`facts.jsonl`), not checkpoint parquet.


def _score_dataframe(df: "pd.DataFrame") -> "pd.DataFrame":
    """Score every numeric column in a dfanalyzer flat view.

    Severity is computed per-column as a percentile of the column's max:
    * trivial  (1) — below 25 % of max
    * low      (2) — 25–50 %
    * medium   (3) — 50–75 %
    * high     (4) — 75–90 %
    * critical (5) — above 90 % of max

    Adds ``<col>_score`` for every numeric column that is not already a
    score column.
    """
    import pandas as pd  # type: ignore

    scored = df.copy()
    for col in df.select_dtypes(include=["number"]).columns:
        if col.endswith("_score"):
            continue
        col_max = scored[col].max()
        if pd.isna(col_max) or col_max == 0:
            scored[f"{col}_score"] = 1
            continue
        # Normalise to 0–1 fraction of column max, then map to 1–5 score
        frac = scored[col] / col_max
        scored[f"{col}_score"] = pd.cut(
            frac,
            bins=[-0.1, 0.25, 0.50, 0.75, 0.90, 1.0],
            labels=[1, 2, 3, 4, 5],
            include_lowest=True,
        ).astype(int)
    return scored


def _diagnose_via_pandas(
    checkpoint_dir: str,
    output_dir: str,
    output_format: str,
) -> Dict[str, Any]:
    """Direct checkpoint diagnosis using pandas — no dfdiagnoser CLI/API needed.

    Reads every ``_flat_view_*.parquet`` file, scores numeric columns,
    writes scored outputs, and returns a structured result dict.
    """
    import pandas as pd  # type: ignore

    flat_views = sorted(glob.glob(os.path.join(checkpoint_dir, "_flat_view_*.parquet")))
    if not flat_views:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"No _flat_view_*.parquet files in {checkpoint_dir}",
            "success": False,
        }

    os.makedirs(output_dir, exist_ok=True)
    scored_count = 0

    for path in flat_views:
        try:
            df = pd.read_parquet(path)
            scored = _score_dataframe(df)
            base = os.path.basename(path).replace(".parquet", "_scored")
            if output_format == "json":
                scored.to_json(os.path.join(output_dir, f"{base}.json"), orient="index")
            elif output_format == "csv":
                scored.to_csv(os.path.join(output_dir, f"{base}.csv"))
            elif output_format == "parquet":
                scored.to_parquet(os.path.join(output_dir, f"{base}.parquet"))
            scored_count += 1
        except Exception as exc:
            # Log but continue — partial scoring is better than none
            pass

    return {
        "returncode": 0,
        "stdout": f"Scored {scored_count} flat view(s) via pandas",
        "stderr": "",
        "success": True,
    }


def _load_scored_views(output_dir: str) -> List[Dict[str, Any]]:
    """Load scored flat view files written by dfdiagnoser."""
    views: List[Dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(output_dir, "*_scored.*"))):
        name = os.path.basename(path)
        try:
            if path.endswith(".json"):
                with open(path) as f:
                    views.append({"view_file": name, "rows": json.load(f)})
            elif path.endswith(".parquet"):
                import pandas as pd  # type: ignore
                df = pd.read_parquet(path)
                views.append({"view_file": name, "rows": df.to_dict(orient="index")})
            elif path.endswith(".csv"):
                import pandas as pd  # type: ignore
                df = pd.read_csv(path, index_col=0)
                views.append({"view_file": name, "rows": df.to_dict(orient="index")})
        except Exception:
            pass
    return views


def _extract_bottlenecks(
    scored_views: List[Dict[str, Any]],
) -> Tuple[Dict[str, int], List[Dict[str, Any]]]:
    """Parse scored flat views into severity counts and ranked bottleneck list."""
    severity_counts: Dict[str, int] = {
        "critical": 0, "high": 0, "medium": 0, "low": 0, "trivial": 0
    }
    bottlenecks: List[Dict[str, Any]] = []

    for view in scored_views:
        view_file = view.get("view_file", "")
        rows = view.get("rows", {})
        for row_key, row in (rows.items() if isinstance(rows, dict) else []):
            score_cols = {
                k.removesuffix("_score"): int(v)
                for k, v in row.items()
                if k.endswith("_score") and v is not None
            }
            if not score_cols:
                continue
            for metric, score in score_cols.items():
                label = _SCORE_LABELS.get(score, "unknown")
                if label in severity_counts:
                    severity_counts[label] += 1
                if score >= 4:  # high or critical
                    bottlenecks.append({
                        "view": view_file,
                        "scope": str(row_key),
                        "metric": metric,
                        "score": score,
                        "severity": label,
                        "description": _describe_metric(metric),
                        "value": row.get(metric),
                    })

    bottlenecks.sort(key=lambda x: x["score"], reverse=True)
    return severity_counts, bottlenecks


def _load_raw_stats(checkpoint_dir: str) -> Optional[Dict[str, Any]]:
    """Load the raw statistics JSON from the checkpoint directory."""
    for path in glob.glob(os.path.join(checkpoint_dir, "_raw_stats_*.json")):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return None


class DFDiagnoserService(MCPService):
    """MCP service that diagnoses I/O bottlenecks from DFAnalyzer checkpoints.

    Wraps the DFDiagnoser library (https://github.com/llnl/DFDiagnoser) and
    exposes a single ``diagnose`` tool.  The tool tries the Python API first and
    falls back to the ``dfdiagnoser`` CLI binary when the package is not
    installed in the current Python environment.

    Attributes:
        diagnoser_subservice (FastMCP): Sub-server named ``"DFDiagnoser"``
            that hosts the ``diagnose`` tool.
    """

    def __init__(self) -> None:
        self.diagnoser_subservice = FastMCP("DFDiagnoser")
        self.session_subservice = FastMCP("DFDiagnoserSession")
        self._register_tools()
        self._register_session_tools()

    def _register_tools(self) -> None:
        """Register ``diagnose`` on :attr:`diagnoser_subservice`."""

        @self.diagnoser_subservice.tool()
        def diagnose(
            facts_dir: Optional[str] = None,
            checkpoint_dir: Optional[str] = None,
            output_dir: Optional[str] = None,
            output_format: str = "json",
            metric_boundaries: Optional[str] = None,
            timeout: int = 300,
        ) -> str:
            """Diagnose I/O bottlenecks from a DFAnalyzer ``output=file`` facts bundle.

            DFDiagnoser is now a pure fact CONSUMER: scoring/motif classification
            happens in DFAnalyzer's own fact pipeline, and DFDiagnoser's
            ``diagnose_file()`` replays the resulting ``facts.jsonl`` bundle into
            classified findings (severity, severity_score, confidence, motif,
            recommendation_bundle). There is no ``diagnose_checkpoint`` — a raw
            ``analyzer.checkpoint`` directory (``_flat_view_*.parquet``) is a
            distinct, incremental-recompute-only cache, not diagnosable directly.

            Typical upstream step::

                dfanalyzer \\
                    trace_path=<trace_dir> \\
                    "analyzer/preset=posix" \\
                    "view_types=[time_range]" \\
                    output=file \\
                    "output.path=<facts_dir>"

            Args:
                facts_dir: Path to the DFAnalyzer ``output=file`` bundle
                    directory (must contain ``facts.jsonl``). Preferred —
                    this is the real, currently-supported diagnosis path.
                checkpoint_dir: LEGACY fallback: a raw ``analyzer.checkpoint``
                    directory (``_flat_view_*.parquet``). Used only if
                    *facts_dir* is not given; scores every numeric column via
                    a local percentile heuristic (not DFDiagnoser's real
                    motif/trend classification) since the real diagnoser
                    cannot consume checkpoint parquet directly. Prefer
                    re-running dfanalyzer with ``output=file`` instead.
                output_dir: Directory where results are written. Defaults to
                    ``<facts_dir>/findings/`` or ``<checkpoint_dir>/scored/``.
                output_format: Format for legacy scored-view output files
                    (checkpoint_dir path only). One of ``"json"`` (default),
                    ``"csv"``, or ``"parquet"``.
                metric_boundaries: Optional JSON object string mapping metric
                    names to their peak-performance reference values, passed
                    to ``diagnose_file()`` (facts_dir path only — DFDiagnoser's
                    CLI does not expose this as a Hydra field).
                    Defaults to ``None`` (no boundary normalisation).
                timeout: Seconds before the diagnosis subprocess is killed.
                    Defaults to ``300``.

            Returns:
                JSON string with keys:
                    * ``status`` (``"ok"`` or ``"error"``).
                    * ``message`` — outcome description.
                    * ``facts_dir`` / ``checkpoint_dir`` — whichever input was analysed.
                    * ``output_dir`` — where results were written.
                    * ``severity_counts`` — dict mapping severity label to count.
                    * ``bottlenecks`` — list of high/critical findings (facts_dir
                      path: full ``DiagnosisFinding`` fields — ``motif``,
                      ``confidence``, ``recommendation_bundle``, ``summary``, etc.;
                      checkpoint_dir path: ``view``/``scope``/``metric``/``score``).
                      Sorted by severity descending, capped at 50 entries.
                    * ``diagnose_result`` — subprocess/API run result dict.

            Raises:
                Returns ``{"status": "error"}`` when:
                    * Neither *facts_dir* nor *checkpoint_dir* is given.
                    * The given directory does not exist or lacks the expected files.
                    * Diagnosis fails via every available path.
            """
            if not facts_dir and not checkpoint_dir:
                return json.dumps({
                    "status": "error",
                    "message": "Provide facts_dir (preferred) or checkpoint_dir (legacy).",
                }, indent=2)

            # ── Preferred path: real diagnose_file() on an output=file bundle ────
            if facts_dir:
                fd = Path(facts_dir)
                if not fd.exists() or not (fd / "facts.jsonl").exists():
                    return json.dumps({
                        "status": "error",
                        "message": (
                            f"No facts.jsonl in {facts_dir}. Run dfanalyzer with "
                            "output=file output.path=<facts_dir> first."
                        ),
                    }, indent=2)

                out_dir = Path(output_dir) if output_dir else (fd / "findings")
                out_dir.mkdir(parents=True, exist_ok=True)
                boundaries = json.loads(metric_boundaries) if metric_boundaries else {}

                run_result = _run_dfdiagnoser_findings(fd, out_dir, timeout, boundaries)
                severity_counts, bottlenecks = _parse_findings_jsonl(out_dir)
                raw_stats = None
                raw_stats_path = fd / "raw_stats.json"
                if raw_stats_path.exists():
                    try:
                        with open(raw_stats_path) as f:
                            raw_stats = json.load(f)
                    except Exception:
                        pass

                total_issues = sum(severity_counts.values())
                critical_high = severity_counts["critical"] + severity_counts["high"]
                msg = (
                    f"Diagnosis complete: {total_issues} finding(s). "
                    f"{critical_high} high/critical issue(s) found."
                )
                if not run_result["success"] and not bottlenecks:
                    return json.dumps({
                        "status": "error",
                        "message": f"Diagnosis failed: {run_result['stderr']}",
                        "facts_dir": facts_dir,
                        "diagnose_result": run_result,
                    }, indent=2)

                return json.dumps({
                    "status": "ok",
                    "message": msg,
                    "facts_dir": facts_dir,
                    "output_dir": str(out_dir),
                    "severity_counts": severity_counts,
                    "bottlenecks": bottlenecks[:50],
                    "raw_stats_summary": (
                        {k: raw_stats[k] for k in list(raw_stats)[:20]}
                        if raw_stats else None
                    ),
                    "diagnose_result": run_result,
                }, indent=2)

            # ── Legacy fallback: raw checkpoint parquet via local pandas scoring ──
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

            run_result = _diagnose_via_pandas(checkpoint_dir, out_dir, output_format)

            scored_views = _load_scored_views(out_dir)
            severity_counts, bottlenecks = _extract_bottlenecks(scored_views)
            raw_stats = _load_raw_stats(checkpoint_dir)

            total_issues = sum(severity_counts.values())
            critical_high = severity_counts["critical"] + severity_counts["high"]
            msg = (
                f"Diagnosis complete (legacy checkpoint path): {total_issues} metric "
                f"observations across {len(scored_views)} view(s). "
                f"{critical_high} high/critical issue(s) found."
            )

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

    def _register_session_tools(self) -> None:
        """Register session-aware diagnosis tools on :attr:`session_subservice`.

        Exposes ``session_diagnose_bottlenecks`` — runs DFAnalyzer + DFDiagnoser
        on the split traces produced by a session ``run_id`` workspace.
        """

        @self.session_subservice.tool()
        def session_diagnose_bottlenecks(
            run_id: str,
            analyzer_preset: str = "posix",
            view_types: Optional[str] = "time_range",
            metric_boundaries: Optional[str] = None,
            timeout: int = 600,
        ) -> str:
            """Diagnose I/O bottlenecks by running DFAnalyzer + DFDiagnoser on session traces.

            Two-phase pipeline:

            **Phase 1 — DFAnalyzer facts bundle**
                Runs ``dfanalyzer`` with ``output=file`` on the split traces in
                ``<workspace>/traces_split/``, writing the deliverable bundle
                (``facts.jsonl``, ``raw_stats.json``) to
                ``<workspace>/dfanalyzer_facts/``. Also writes an independent
                ``analyzer.checkpoint`` cache to ``<workspace>/dfanalyzer_checkpoint/``
                (incremental-recompute only — not consumed by DFDiagnoser).

            **Phase 2 — DFDiagnoser**
                Replays ``facts.jsonl`` via ``diagnose_file()`` into classified
                findings (severity, severity_score, confidence, motif,
                recommendation_bundle — scoring already happened in the analyzer's
                fact pipeline). Findings are written to
                ``<workspace>/diagnosis/findings/findings.jsonl`` and a bottleneck
                summary is saved to ``<workspace>/diagnosis.json``.

            Severity levels: ``trivial`` / ``low`` / ``medium`` / ``high`` /
            ``critical`` (``high``/``critical`` surface as bottlenecks), each
            paired with a continuous ``severity_score`` (0.0-1.0) and a
            ``confidence`` value from DFDiagnoser's trend/motif classification.

            Side effects:
                * Creates ``<workspace>/dfanalyzer_facts/`` and ``<workspace>/dfanalyzer_checkpoint/``.
                * Creates ``<workspace>/diagnosis/findings/``.
                * Writes ``<workspace>/diagnosis.json`` with the bottleneck summary.
                * Persists ``{"step": "bottlenecks_diagnosed", ...}`` to ``session.json``.
                * Writes an artifact log at step 15.

            Args:
                run_id: Session identifier returned by ``session_create``.
                analyzer_preset: DFAnalyzer preset(s).  ``"posix"`` covers POSIX
                    file I/O; ``"dlio"`` covers deep-learning I/O workloads;
                    ``"generic"`` auto-discovers distinct ``cat`` values in the
                    trace and builds one layer per category — use it for
                    custom app-annotation categories or mixed/unknown workloads
                    that don't fit ``posix``/``dlio``. Accepts a comma-separated
                    list (e.g. ``"dlio,generic"``) to run and merge multiple
                    presets in one call — the pipeline's own diagnose step does
                    this automatically for DL workloads (``dlio,generic``) via
                    ``session/detection.py``'s ``_detect_analyzer_presets``.
                    Defaults to ``"posix"``.
                view_types: Comma-separated DFAnalyzer view type(s).
                    Defaults to ``"time_range"``.
                metric_boundaries: Optional JSON object string mapping metric names
                    to hardware peak values for bandwidth/IOPS normalisation,
                    passed to ``diagnose_file()`` (Python-API path only).
                    Defaults to ``None``.
                timeout: Seconds before each subprocess phase is killed.
                    Defaults to ``600``.

            Returns:
                JSON string with keys:
                    * ``status`` (``"ok"`` or ``"error"``).
                    * ``message`` — outcome description.
                    * ``diagnosis_file`` — path to ``diagnosis.json``.
                    * ``facts_dir`` — dfanalyzer output=file bundle directory.
                    * ``checkpoint_dir`` — dfanalyzer incremental-recompute cache directory.
                    * ``severity_counts`` — per-severity finding counts.
                    * ``bottlenecks`` — list of high/critical findings (up to 50),
                      each with the full ``DiagnosisFinding`` fields (``motif``,
                      ``confidence``, ``recommendation_bundle``, ``summary``, etc.).
                    * ``phases`` — subprocess result dicts for debugging.

            Raises:
                Returns ``{"status": "error"}`` when:
                    * ``traces_split/`` does not exist (run ``session_split_traces`` first).
                    * dfanalyzer fails to produce ``facts.jsonl``.
                    * DFDiagnoser is not installed and no CLI binary is found.
            """
            return _session_diagnose_bottlenecks_impl(
                run_id=run_id,
                analyzer_preset=analyzer_preset,
                view_types=view_types,
                metric_boundaries=metric_boundaries,
                timeout=timeout,
            )

    def execute(self, data: dict) -> Optional[str]:
        return "Use the diagnose tool to identify I/O bottlenecks from a DFAnalyzer output=file facts bundle."

    @property
    def name(self) -> str:
        return "dfdiagnoser"


MCPServiceFactory.register("dfdiagnoser", DFDiagnoserService())
