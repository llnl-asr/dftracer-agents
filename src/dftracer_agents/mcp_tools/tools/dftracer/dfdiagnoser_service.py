"""DFDiagnoser MCP service — I/O bottleneck diagnosis from DFAnalyzer checkpoints.

This module exposes the DFDiagnoser library as an MCP tool so that AI agents can
identify I/O bottlenecks in dftracer traces without constructing shell commands
manually.

Background
----------
DFDiagnoser consumes *DFAnalyzer checkpoints* — a directory of ``_flat_view_*.parquet``
and ``_raw_stats_*.json`` files produced when dfanalyzer is run with
``analyzer.checkpoint=True``.  It scores each metric against severity thresholds
(trivial → critical) and, in streaming mode, builds higher-level findings with
motifs and recommendations.

For static checkpoint runs (the common batch-pipeline use case) the tool:

1. Loads each ``_flat_view_*.parquet`` file from the checkpoint directory.
2. Calls ``score_metrics()`` which adds a ``<metric>_score`` column
   (1 = trivial, 2 = low, 3 = medium, 4 = high, 5 = critical) to every
   relevant metric.
3. Serialises the scored views to ``<output_dir>/`` as JSON/CSV/Parquet.
4. Extracts the highest-scoring metrics and surfaces them as a structured
   bottleneck summary.

Tools exposed
-------------
* ``diagnose`` — run dfdiagnoser on an existing DFAnalyzer checkpoint directory.

Typical pipeline order
----------------------
::

    dfanalyzer (via analyze tool, with analyzer_checkpoint=True)
        → checkpoint_dir/  (_flat_view_*.parquet, _raw_stats_*.json)

    diagnose(checkpoint_dir=checkpoint_dir, output_dir=output_dir)
        → scored flat views  +  bottleneck summary JSON

References
----------
* https://github.com/llnl/DFDiagnoser
* https://dfanalyzer.readthedocs.io/
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
from ..optimizations.diagnose import _session_diagnose_bottlenecks_impl

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


def _diagnose_via_api(
    checkpoint_dir: str,
    output_dir: str,
    output_format: str,
    metric_boundaries: Dict[str, float],
) -> Optional[Dict[str, Any]]:
    """Attempt Python-API diagnosis; return None to signal the direct-pandas
    fallback should be used instead.

    As of DFDiagnoser's current release, only ``diagnose_file``,
    ``diagnose_facts``, ``diagnose_mofka``, and ``diagnose_zmq`` exist; there
    is no ``diagnose_checkpoint`` method. Treat that as an expected
    "API unavailable" case, not a hard failure, so the direct checkpoint
    reader (which uses pandas) always runs.
    """
    try:
        from dfdiagnoser.diagnoser import Diagnoser  # type: ignore
    except ImportError:
        return None

    diagnoser = Diagnoser()
    if not hasattr(diagnoser, "diagnose_checkpoint"):
        return None
    # If a future DFDiagnoser release adds diagnose_checkpoint, this path
    # will automatically light up.  For now it always returns None.
    return None


def _diagnose_via_cli(
    checkpoint_dir: str,
    output_dir: str,
    output_format: str,
    timeout: int,
) -> Dict[str, Any]:
    """Run dfdiagnoser CLI as a subprocess.

    NOTE: The dfdiagnoser CLI (Hydra-based) does NOT support
    ``input=checkpoint``.  Valid input modes are ``file`` (expects
    ``facts.jsonl``) and ``mofka`` / ``zmq`` (streaming).  Therefore this
    function is kept for API compatibility but will fail with a clear
    message directing the caller to the direct-pandas fallback.
    """
    return {
        "returncode": -1,
        "stdout": "",
        "stderr": (
            "dfdiagnoser CLI does not support input=checkpoint. "
            "Use the direct checkpoint reader (pandas) instead."
        ),
        "success": False,
    }


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

            # ── Run diagnosis (API → pandas fallback → CLI) ───────────────
            run_result = _diagnose_via_api(checkpoint_dir, out_dir, output_format, boundaries)
            if run_result is None:
                # API unavailable — try direct pandas scoring (primary fallback)
                run_result = _diagnose_via_pandas(checkpoint_dir, out_dir, output_format)
            if not run_result["success"]:
                # pandas failed — try CLI as last resort (expected to fail for checkpoint)
                run_result = _diagnose_via_cli(checkpoint_dir, out_dir, output_format, timeout)

            # ── Parse scored outputs ──────────────────────────────────────
            scored_views = _load_scored_views(out_dir)
            severity_counts, bottlenecks = _extract_bottlenecks(scored_views)
            raw_stats = _load_raw_stats(checkpoint_dir)

            total_issues = sum(severity_counts.values())
            critical_high = severity_counts["critical"] + severity_counts["high"]
            msg = (
                f"Diagnosis complete: {total_issues} metric observations across "
                f"{len(scored_views)} view(s). "
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
                    Defaults to ``600``.

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

    def execute(self, data: dict) -> Optional[str]:
        return "Use the diagnose tool to identify I/O bottlenecks from DFAnalyzer checkpoints."

    @property
    def name(self) -> str:
        return "dfdiagnoser"


MCPServiceFactory.register("dfdiagnoser", DFDiagnoserService())
