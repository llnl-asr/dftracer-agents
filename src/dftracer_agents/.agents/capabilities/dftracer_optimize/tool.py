"""dftracer_optimize: the optimization loop of the dftracer pipeline (plan, trials, L1/L2/L3 level tools, iteration, proposal generation) and a deterministic validator of proposals and before/after comparisons (dftracer_optimize_check)."""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from datetime import timezone
from dftracer_agents.mcp_tools.tools.optimizations.diagnose import _session_diagnose_bottlenecks_impl
from dftracer_agents.mcp_tools.tools.optimizations.iteration import _DL_ALWAYS_ON_METRICS
from dftracer_agents.mcp_tools.tools.optimizations.iteration import _L1_STRATEGIES
from dftracer_agents.mcp_tools.tools.optimizations.iteration import _L2_STRATEGIES
from dftracer_agents.mcp_tools.tools.optimizations.iteration import _L3_STRATEGIES
from dftracer_agents.mcp_tools.tools.optimizations.iteration import _load_external_bottlenecks
from dftracer_agents.mcp_tools.tools.optimizations.levels import _L1_STRATEGIES
from dftracer_agents.mcp_tools.tools.optimizations.levels import _L2_STRATEGIES
from dftracer_agents.mcp_tools.tools.optimizations.levels import _L3_STRATEGIES
from dftracer_agents.mcp_tools.tools.optimizations.memory import _memory_reflect
from dftracer_agents.mcp_tools.tools.optimizations.memory import _memory_retrieve
from dftracer_agents.mcp_tools.tools.optimizations.memory import _memory_write
from dftracer_agents.mcp_tools.tools.optimizations.orchestrator import _addressable_layers
from dftracer_agents.mcp_tools.tools.optimizations.orchestrator import _trial_id
from dftracer_agents.mcp_tools.tools.optimizations.strategies import _bottleneck_search_queries
from dftracer_agents.mcp_tools.tools.optimizations.strategies import _build_sys_context
from dftracer_agents.mcp_tools.tools.optimizations.strategies import _category_sort_key
from dftracer_agents.mcp_tools.tools.optimizations.strategies import _fetch_arxiv_papers
from dftracer_agents.mcp_tools.tools.optimizations.strategies import _gen_level_proposals
from dftracer_agents.mcp_tools.tools.optimizations.strategies import _metric_category
from dftracer_agents.mcp_tools.tools.session.install import _dftracer_info_uncompressed_bytes
from dftracer_agents.mcp_tools.tools.session.install import _dftracer_utils_comparator
from dftracer_agents.mcp_tools.tools.session.install import _dftracer_utils_split
from dftracer_agents.mcp_tools.tools.session.session_tools import _generate_patch
from dftracer_agents.mcp_tools.tools.session.session_tools import _run_patches_dir
from dftracer_agents.mcp_tools.tools.session.session_tools import _run_source_dir
from dftracer_agents.mcp_tools.tools.session.session_tools import _session_build_annotated_impl
from dftracer_agents.mcp_tools.tools.session.session_tools import _session_collect_system_info_impl
from dftracer_agents.mcp_tools.tools.session.session_tools import _session_run_with_dftracer_impl
from dftracer_agents.mcp_tools.tools.session.session_tools import _session_split_traces_impl
from dftracer_agents.mcp_tools.tools.session.session_tools import _snapshot_source
from dftracer_agents.mcp_tools.tools.session.workspace import _err
from dftracer_agents.mcp_tools.tools.session.workspace import _load_state
from dftracer_agents.mcp_tools.tools.session.workspace import _ok
from dftracer_agents.mcp_tools.tools.session.workspace import _run
from dftracer_agents.mcp_tools.tools.session.workspace import _save_state
from dftracer_agents.mcp_tools.tools.session.workspace import _write_artifact_log
from dftracer_agents.mcp_tools.tools.session.workspace import _ws
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
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

# --------------------------------------------------------------------------- dftracer_optimize_check
_STATUSES = ("proposed", "applied", "not-applied", "deferred")


def _run_side(v):
    """(identifier, metric, value) of one side of a comparison."""
    if isinstance(v, dict):
        ident = v.get("run_id") or v.get("run") or v.get("path") or v.get("trace_dir") or v.get("name") or ""
        val = v.get("value", v.get("metric_value"))
        return str(ident), str(v.get("metric") or ""), val
    return str(v or ""), "", None


def _optimize_check(proposals_json: str, comparison_json: str, stage: str) -> str:
    checks: List[dict] = []
    table = stage.strip().lower() != "generated"
    if proposals_json.strip():
        try:
            rows = [r for r in _rows(_parse_json_arg(proposals_json)) if isinstance(r, dict)]
        except ValueError as e:
            _add(checks, "proposals_parse", False, str(e), "pass the proposals list (or the opt_proposal_table / session_generate_optimization_proposals JSON)")
            rows = None
        if rows is not None:
            _add(checks, "proposals_present", bool(rows), f"{len(rows)} proposal(s)", "generate proposals from the diagnosed bottlenecks first")
            no_level = [i for i, r in enumerate(rows, 1) if not _level_of(r)]
            _add(checks, "level_is_L1_L2_L3", not no_level, f"rows without level L1/L2/L3: {no_level}" if no_level else "ok",
                 "every proposal names its layer: L1 application, L2 software/middleware, L3 system")
            uncited = [f"row {i}: {p}" for i, r in enumerate(rows, 1) if (p := _citation_problem(r.get("citation")))]
            _add(checks, "citation_backed", not uncited, "; ".join(uncited) or "every proposal is cited",
                 "cite a paper (arXiv/DOI) > docs > web; session:<run_id> only for a result measured in this pipeline")
            do_less = [f"row {i}: {_DO_LESS_RX.search(_row_text(r)).group(0)!r}" for i, r in enumerate(rows, 1) if _DO_LESS_RX.search(_row_text(r))]
            _add(checks, "never_do_less", not do_less, "; ".join(do_less) or "ok",
                 "doing less (fewer epochs/steps, smaller input, skipped output or checkpoints) is not an optimization")
            if table:
                no_imp = [i for i, r in enumerate(rows, 1) if not (_num(r.get("app_impact_pct")) and _num(r.get("system_impact_pct")))]
                _add(checks, "impact_fields", not no_imp, f"rows without numeric app_impact_pct/system_impact_pct: {no_imp}" if no_imp else "ok",
                     "fill both potential impact fields (0 when not applicable, never missing)")
                bad_status = [i for i, r in enumerate(rows, 1) if str(r.get("status", "proposed")) not in _STATUSES]
                _add(checks, "status_valid", not bad_status, f"rows with unknown status: {bad_status}" if bad_status else "ok",
                     f"status is one of {_STATUSES}")
                no_reason = [i for i, r in enumerate(rows, 1) if r.get("status") in ("not-applied", "deferred") and not str(r.get("skip_reason") or "").strip()]
                _add(checks, "skip_reason_for_not_applied", not no_reason, f"rows without skip_reason: {no_reason}" if no_reason else "ok",
                     "every considered-but-not-applied candidate stays in the table with its skip_reason")
                unmeasured = [i for i, r in enumerate(rows, 1) if r.get("status") == "applied" and not _num(r.get("actual_app_impact_pct"))]
                _add(checks, "applied_rows_measured", not unmeasured, f"applied rows without actual_app_impact_pct: {unmeasured}" if unmeasured else "ok",
                     "measure the applied change (comparator against the baseline) and fill actual_app_impact_pct, then opt_kb_record it")
    if comparison_json.strip():
        try:
            c = _parse_json_arg(comparison_json)
        except ValueError as e:
            c = None
            _add(checks, "comparison_parse", False, str(e), "pass {\"baseline\": {...}, \"variant\": {...}}")
        if c is not None:
            c = c if isinstance(c, dict) else {}
            b = c.get("baseline")
            v = next((c[k] for k in ("variant", "optimized", "opt", "candidate") if c.get(k) is not None), None)
            bi, bm, bv = _run_side(b)
            vi, vm, vv = _run_side(v)
            _add(checks, "comparison_has_both_runs", bool(bi and vi), f"baseline={bi or '(missing)'} variant={vi or '(missing)'}",
                 "a comparison needs both the baseline run and the optimized run")
            if bi and vi:
                _add(checks, "comparison_runs_distinct", bi != vi, "distinct runs" if bi != vi else f"same run twice: {bi}",
                     "compare the optimized run against its own baseline, not against itself")
            if isinstance(b, dict) or isinstance(v, dict):
                _add(checks, "comparison_values", _num(bv) and _num(vv), f"baseline value={bv} variant value={vv}",
                     "give the measured metric value of both runs")
                _add(checks, "comparison_same_metric", not (bm and vm) or bm == vm, f"{bm or '?'} vs {vm or '?'}",
                     "compare the same metric on both runs")
    return _verdict(checks)


def register(mcp):
    """Register this capability's tools on *mcp*."""

    @mcp.tool()
    def session_optimization_plan(run_id: str, iteration: int = -1) -> str:
        """Build a Layer x Component x Scale trial matrix from the latest diagnosed bottlenecks.

        Reads ``bottlenecks`` from ``state["optimization_history"][iteration]``
        (populated by ``session_optimization_iteration``) and classifies each
        one along two axes:

        * **Component** (I/O, communication, memory, compute) via the existing
          ``_metric_category``/``_category_sort_key`` ordering — bottlenecks
          are greedily prioritized I/O first, then communication, memory,
          compute, with severity as the tiebreaker within a category.
        * **Layer** (L1 app / L2 middleware / L3 filesystem) — a bottleneck's
          metric is "addressable" at layer L*n* if ``_L{n}_STRATEGIES``
          (from ``strategies.py``) has a proposal table entry for it.

        Every ``(layer, metric)`` combination becomes one trial entry, scaled
        ``"small"`` initially. Trials for *different* metrics, or the *same*
        metric at *different* layers, are marked ``isolated: true`` since they
        change disjoint config/source surfaces and can be run in any order.
        Trials for the *same* metric at the *same* layer (multiple candidate
        strategies) are grouped so only one should be applied at a time to
        keep impact attribution clean.

        This tool only plans — it does not run anything or apply any change.

        Args:
            run_id: Session identifier.
            iteration: Which optimization iteration's bottlenecks to plan
                against (-1 = latest).

        Returns:
            JSON with keys: ``status``, ``message``, ``iteration``, ``trials``
            (list of ``{trial_id, layer, component, metric, severity, scale,
            isolated}``), ``trial_count``.
        """
        state = _load_state(run_id)
        history = state.get("optimization_history", [])
        if not history:
            return _err("No optimization iterations — run session_optimization_iteration first.")

        idx = iteration if iteration >= 0 else len(history) - 1
        if idx < 0 or idx >= len(history):
            return _err(f"iteration {iteration} out of range (0..{len(history)-1})")
        bottlenecks = history[idx].get("bottlenecks", [])

        trials: List[Dict[str, Any]] = []
        for bn in sorted(bottlenecks, key=_category_sort_key):
            metric = bn.get("metric", "")
            severity = bn.get("severity", "trivial")
            component = _metric_category(metric)
            layers = _addressable_layers(metric)
            if not layers:
                # No known strategy table entry — still record as an L1
                # candidate so the agent can investigate manually.
                layers = ["L1"]
            for layer in layers:
                trials.append({
                    "trial_id": _trial_id(layer, component, metric, "small"),
                    "layer": layer,
                    "component": component,
                    "metric": metric,
                    "severity": severity,
                    "scale": "small",
                    "isolated": True,
                })

        state_key = f"optimization_plan_{idx}"
        _save_state(run_id, {
            state_key: {
                "iteration": idx,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "trials": trials,
            },
        })

        return _ok(
            f"Trial plan built for iteration {idx}: {len(trials)} isolated trial(s) "
            f"across {len({t['layer'] for t in trials})} layer(s) and "
            f"{len({t['component'] for t in trials})} component(s).",
            iteration=idx,
            trials=trials,
            trial_count=len(trials),
        )

    @mcp.tool()
    def session_optimization_apply_trial(
        run_id: str,
        trial_id: str,
        iteration: int,
        result_metric_delta: float,
        notes: str = "",
    ) -> str:
        """Record the measured impact of one isolated trial and update the greedy-best config.

        The calling agent runs the actual trial itself (e.g. via
        ``session_run_l1_iteration`` for an L1 change, or
        ``session_run_with_dftracer`` with a modified env/config for L2/L3),
        measures the resulting change in the target metric, then reports it
        here as *result_metric_delta* (negative = improvement, by convention,
        for time/latency-style metrics — the sign convention is up to the
        caller as long as it is applied consistently across trials for the
        same metric).

        Appends the result to ``state["trial_results"]``, then recomputes the
        greedy-best trial *per (layer, component, metric)* group seen so far
        (most negative delta wins). When every trial belonging to a
        ``(layer, metric)`` group at ``scale="small"`` has been reported, the
        group is flagged ``ready_for_full_scale: true`` so the caller knows to
        promote the winning trial to a ``scale="full"`` confirmation run at
        full allocation.

        Args:
            run_id: Session identifier.
            trial_id: Identifier from ``session_optimization_plan``'s
                ``trials[].trial_id`` (format ``"L{n}.{component}.{metric}.{scale}"``).
            iteration: Iteration index the trial plan was generated for.
            result_metric_delta: Measured change in the target metric after
                applying this trial in isolation.
            notes: Optional free-text description of what was changed.

        Returns:
            JSON with keys: ``status``, ``message``, ``trial_id``,
            ``best_per_group`` (mapping ``"{layer}.{metric}"`` -> best trial
            record so far), ``ready_for_full_scale`` (list of group keys whose
            small-scale trials are all reported).
        """
        state = _load_state(run_id)
        plan = state.get(f"optimization_plan_{iteration}")
        if not plan:
            return _err(
                f"No trial plan found for iteration {iteration} — call "
                "session_optimization_plan first.",
            )

        known_ids = {t["trial_id"] for t in plan["trials"]}
        planned = next((t for t in plan["trials"] if t["trial_id"] == trial_id), None)
        if planned is None:
            return _err(f"Unknown trial_id {trial_id!r} for iteration {iteration}.")

        results: List[Dict[str, Any]] = state.get("trial_results", [])
        results = [r for r in results if r.get("trial_id") != trial_id]
        record = {
            "trial_id": trial_id,
            "layer": planned["layer"],
            "component": planned["component"],
            "metric": planned["metric"],
            "scale": planned["scale"],
            "delta": result_metric_delta,
            "notes": notes,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        results.append(record)
        _save_state(run_id, {"trial_results": results})

        # Greedy best-per-group (most negative delta = best improvement).
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in results:
            key = f"{r['layer']}.{r['metric']}"
            groups.setdefault(key, []).append(r)

        best_per_group = {
            key: min(recs, key=lambda r: r["delta"])
            for key, recs in groups.items()
        }

        # A group is ready for full-scale confirmation once every planned
        # small-scale trial_id sharing its (layer, metric) has been reported.
        ready: List[str] = []
        for key, recs in groups.items():
            layer, metric = key.split(".", 1)
            planned_ids = {
                t["trial_id"] for t in plan["trials"]
                if t["layer"] == layer and t["metric"] == metric and t["scale"] == "small"
            }
            reported_ids = {r["trial_id"] for r in recs if r["scale"] == "small"}
            if planned_ids and planned_ids <= reported_ids:
                ready.append(key)

        return _ok(
            f"Trial {trial_id} recorded (delta={result_metric_delta}).",
            trial_id=trial_id,
            best_per_group=best_per_group,
            ready_for_full_scale=ready,
        )

    @mcp.tool()
    def session_optimization_iteration(
        run_id: str,
        command: str,
        app_name: str = "app",
        data_dir: str = "all",
        timeout: int = 600,
        env_extra: Optional[str] = None,
        optimization_applied: str = "",
        rebuild: bool = True,
        max_search_attempts: int = 10,
        papers_per_query: int = 3,
    ) -> str:
        """Run one iteration of the build → profile → diagnose → search optimization loop.

        Each call executes the following pipeline in sequence:

        1. **Build** (optional) — rebuild the annotated binary so any source
           changes applied between calls take effect.  Skip with
           ``rebuild=False`` to re-profile without rebuilding.
        2. **Profile** — run *command* with dftracer tracing enabled.
        3. **Split** — compact raw ``.pfw`` traces.
        4. **Diagnose** — run DFAnalyzer + DFDiagnoser and score bottlenecks.
        5. **System context** — collect or re-read ``system_config.json`` so
           hardware details (CPU arch, filesystem type, network) are available
           to refine search queries.
        6. **Literature search** — bottlenecks are ranked and addressed in the
           canonical order **I/O -> communication -> memory -> compute**
           (severity is only the tiebreaker *within* a category, so a
           critical compute issue never jumps ahead of a medium I/O issue).
           For deep-learning workloads, two additional dimensions are always
           evaluated regardless of severity: (a) application dataloader /
           epoch-time performance, and (b) filesystem bandwidth/utilization
           for the storage the run is on. For the resulting top bottlenecks,
           search arXiv with up to *max_search_attempts* progressively
           fuzzier queries that combine the bottleneck behaviour **and** the
           system hardware context:

           * Attempts 1-2: most specific phrase + system context.
           * Attempts 3-8: synonym phrase pairs with/without system context.
           * Attempts 9-10: broadest fallback ("I/O optimization {sys_context}",
             then generic "parallel I/O performance optimization HPC").

           A bottleneck is marked **unsolved** if no papers are found after all
           *max_search_attempts* queries.  The tool reports what it could not
           find so the agent knows where to ask the user for guidance.

        7. **Compare** — diff the bottleneck severity table against the previous
           iteration (stored in ``session.json`` under ``optimization_history``).

        **Typical agent loop**::

            # Baseline — first iteration
            r0 = session_optimization_iteration(
                run_id, command, optimization_applied="baseline")
            # Agent reads r0.literature and r0.unsolved, applies a source change.
            r1 = session_optimization_iteration(
                run_id, command, optimization_applied="increased write buffer to 4 MiB")
            # r1.delta shows which bottlenecks improved / regressed.
            # Repeat until r1.bottlenecks is empty or delta shows no improvement.

        Args:
            run_id:               Session identifier.
            command:              Benchmark command passed to
                                  ``session_run_with_dftracer``.
            app_name:             Trace split file prefix.
            data_dir:             ``DFTRACER_DATA_DIR`` value (default ``"all"``).
            timeout:              Seconds allowed for profiled run and diagnosis.
            env_extra:            Extra env vars as JSON (same format as
                                  ``session_run_with_dftracer``).
            optimization_applied: Human-readable label for this iteration's change.
                                  Use ``"baseline"`` on the first call.
            rebuild:              If ``True``, rebuild before profiling.
            max_search_attempts:  Maximum arXiv queries per bottleneck (1-10,
                                  default 10).  Stops early when papers are found.
            papers_per_query:     Papers to fetch per query attempt (1-5, default 3).

        Returns:
            JSON string with keys:

            * ``status``          — ``"ok"`` or ``"error"``.
            * ``iteration``       — zero-based iteration index.
            * ``optimization``    — echoed *optimization_applied* label.
            * ``build``           — build step summary (or ``null``).
            * ``profile``         — profiling step summary.
            * ``diagnosis``       — DFDiagnoser summary (severity counts).
            * ``system_context``  — hardware context string used for searches.
            * ``top5_bottlenecks``— top-5 bottlenecks by severity.
            * ``literature``      — list of per-bottleneck search results:
              ``{"bottleneck": …, "queries_tried": N, "papers": [...]}``.
            * ``unsolved``        — bottlenecks for which no papers were found
              after all *max_search_attempts* attempts, with the queries tried.
            * ``delta``           — severity delta vs previous iteration:
              ``{"improved": [...], "regressed": [...], "resolved": [...],
              "new": [...], "unchanged": [...]}``.
            * ``recommendation``  — plain-text guidance for the next step.
        """
        import json as _json
        import shutil as _sh

        state = _load_state(run_id)
        history: list = state.get("optimization_history", [])
        iteration = len(history)
        _SEV = {"trivial": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

        # ── Step 1: (re)build annotated binary ──────────────────────────────
        build_summary = None
        if rebuild:
            raw = _session_build_annotated_impl(run_id=run_id)
            build_result = _json.loads(raw)
            if build_result.get("status") != "ok":
                return _json.dumps({
                    "status": "error",
                    "message": f"Build failed at iteration {iteration}: "
                               + build_result.get("message", ""),
                    "build": build_result,
                    "iteration": iteration,
                })
            build_summary = {
                "returncode": build_result.get("returncode", 0),
                "duration_s": build_result.get("duration_s"),
            }

        # ── Per-iteration artifact directory ─────────────────────────────────
        ws = _ws(run_id)
        run_name = f"opt{iteration}"
        iter_dir = ws / run_name
        iter_dir.mkdir(exist_ok=True)
        # Canonical run structure: traces/raw, traces/compact, source/, patches/
        iter_traces_dir = iter_dir / "traces" / "raw"
        iter_split_dir  = iter_dir / "traces" / "compact"
        iter_traces_dir.mkdir(parents=True, exist_ok=True)
        iter_split_dir.mkdir(parents=True, exist_ok=True)

        # ── Source snapshot + patch ──────────────────────────────────────────
        # Snapshot current annotated source into opt{N}/source/.
        # Generate a patch vs the previous iteration's source snapshot.
        ann_src = ws / "annotated"
        if ann_src.exists():
            source_dest = _run_source_dir(ws, run_name)
            _snapshot_source(ann_src, source_dest)
            # Determine previous run: opt{N-1}/source/ or annotated/source/
            if iteration == 0:
                prev_src = ws / "annotated" / "source"
                if not prev_src.exists():
                    prev_src = ws / "annotated"
                    # Try the structured annotated run snapshot
                    if (ws / "annotated" / "source").exists():
                        prev_src = ws / "annotated" / "source"
                # For iteration 0, diff vs the annotated run's source snapshot
                prev_src = ws / "annotated" / "source" if (ws / "annotated" / "source").exists() else None
                if prev_src:
                    patch_file = _run_patches_dir(ws, run_name) / "from_annotated.patch"
                    _generate_patch(prev_src, source_dest, patch_file)
                else:
                    patch_file = _run_patches_dir(ws, run_name) / "from_annotated.patch"
                    patch_file.write_text("# no annotated/source/ snapshot available\n")
            else:
                prev_src = ws / f"opt{iteration - 1}" / "source"
                if prev_src.exists():
                    patch_file = _run_patches_dir(ws, run_name) / f"from_opt{iteration - 1}.patch"
                    _generate_patch(prev_src, source_dest, patch_file)

        # ── Step 2: profile run ──────────────────────────────────────────────
        # Traces go directly into ws/<run_name>/traces/raw/ via run_name routing
        raw = _session_run_with_dftracer_impl(
            run_id=run_id,
            command=command,
            subfolder="build_ann",
            data_dir=data_dir,
            timeout=timeout,
            env_extra=env_extra,
            run_name=run_name,
        )
        profile_result = _json.loads(raw)
        if profile_result.get("status") != "ok":
            return _json.dumps({
                "status": "error",
                "message": f"Profile run failed at iteration {iteration}: "
                           + profile_result.get("message", ""),
                "profile": profile_result,
                "iteration": iteration,
            })
        # ── Step 3: split traces into per-iteration compact dir ──────────────
        # Reads from ws/<run_name>/traces/raw/, writes to ws/<run_name>/traces/compact/
        _session_split_traces_impl(run_id=run_id, app_name=app_name, run_name=run_name)
        profile_summary = {
            "returncode": profile_result.get("returncode"),
            "elapsed_s":  profile_result.get("elapsed_s"),
            "trace_files": [str(p) for p in iter_traces_dir.glob("*.pfw.gz")]
                            + [str(p) for p in iter_traces_dir.glob("*.pfw")],
        }

        # ── Step 3b: compare against previous iteration traces ───────────────
        comparison: Dict[str, Any] = {}
        if iteration > 0:
            prev_split = ws / f"opt{iteration - 1}" / "traces" / "compact"
            if prev_split.exists() and any(prev_split.glob("*.pfw.gz")):
                try:
                    cmp_raw = _dftracer_utils_comparator(
                        baseline=str(prev_split),
                        variant=str(iter_split_dir),
                        query='cat == "POSIX" OR cat == "STDIO" OR cat == "C_APP"',
                        group_by_dims="cat,name",
                        output_format="json",
                        threshold_pct=5.0,
                    )
                    cmp_result = (
                        _json.loads(cmp_raw["stdout"])
                        if cmp_raw["success"] and cmp_raw["stdout"].strip()
                        else {"error": cmp_raw["stderr"][:500] or "empty output",
                              "returncode": cmp_raw["returncode"]}
                    )
                    comparison = {
                        "baseline_iter": iteration - 1,
                        "variant_iter":  iteration,
                        "baseline_dir":  str(prev_split),
                        "variant_dir":   str(iter_split_dir),
                        "result":        cmp_result,
                    }
                    # Persist comparison to per-iteration dir
                    (iter_dir / "comparison.json").write_text(
                        _json.dumps(comparison, indent=2)
                    )
                except Exception as _cmp_err:
                    comparison = {"error": str(_cmp_err)}

        # ── Step 4: diagnose bottlenecks ────────────────────────────────────
        # Clear stale checkpoint so each iteration scores only its own traces
        _ckpt = ws / "dfanalyzer_checkpoint"
        if _ckpt.exists():
            import shutil as _sh2
            _sh2.rmtree(str(_ckpt))
        _ckpt.mkdir(exist_ok=True)
        # Use the DLIO preset for ML/DL workloads so dfanalyzer surfaces
        # dataloader/compute-specific metrics (e.g. data_loader_ops_slope,
        # compute_ops_slope) instead of only generic POSIX metrics.
        _preset = "dlio" if (state.get("frameworks") or state.get("ml_frameworks_list")) else "posix"
        raw = _session_diagnose_bottlenecks_impl(
            run_id=run_id, timeout=timeout, traces_dir=str(iter_split_dir),
            analyzer_preset=_preset,
        )
        diag_result = _json.loads(raw)
        current_bottlenecks: list = diag_result.get("bottlenecks", [])
        diag_summary = {
            "severity_counts": diag_result.get("severity_counts", {}),
            "bottleneck_count": len(current_bottlenecks),
        }

        # ── Step 5: system context ───────────────────────────────────────────
        sys_cfg_path = ws / "system_config.json"
        if sys_cfg_path.exists():
            try:
                sys_info = _json.loads(sys_cfg_path.read_text())
            except Exception:
                sys_info = {}
        else:
            raw_sys = _session_collect_system_info_impl(run_id=run_id)
            sys_info = _json.loads(raw_sys)
        sys_context = _build_sys_context(sys_info)

        # ── Step 6: literature search — top bottlenecks ─────────────────────
        # Bottlenecks are addressed in the canonical order I/O -> communication
        # -> memory -> compute (severity is only the tiebreaker within a
        # category), so a critical compute issue never jumps ahead of a
        # medium-severity I/O issue.
        ranked = sorted(current_bottlenecks, key=_category_sort_key)
        top5 = ranked[:5]

        # For deep-learning workloads, always evaluate two additional
        # dimensions regardless of severity ranking: (1) application
        # dataloader / epoch-time performance, and (2) filesystem bandwidth /
        # utilization for the storage the run is on.  If a matching
        # bottleneck exists but fell outside the top5, pull it in.
        is_dl_workload = bool(
            state.get("frameworks") or state.get("ml_frameworks_list")
            or any(
                any(dl_met in b.get("metric", "") for dl_met in _DL_ALWAYS_ON_METRICS["dataloader_epoch"])
                for b in current_bottlenecks
            )
        )
        if is_dl_workload:
            top5_metrics = {b.get("metric", "") for b in top5}
            for dim, metric_fragments in _DL_ALWAYS_ON_METRICS.items():
                if any(any(frag in m for frag in metric_fragments) for m in top5_metrics):
                    continue  # already represented
                candidate = next(
                    (b for b in ranked
                     if any(frag in b.get("metric", "") for frag in metric_fragments)),
                    None,
                )
                if candidate is not None:
                    top5.append({**candidate, "dl_dimension": dim})

        max_attempts = max(1, min(10, max_search_attempts))
        n_papers     = max(1, min(5, papers_per_query))
        seen_titles: set = set()
        literature: List[Dict[str, Any]] = []
        unsolved:   List[Dict[str, Any]] = []

        for bn in top5:
            metric      = bn.get("metric", "")
            description = bn.get("description", "")
            severity    = bn.get("severity", "")

            found_papers: List[Dict[str, Any]] = []
            queries_tried: List[str] = []

            # ── Agentic RAG: read Tier-2 project memory before searching ────
            # A citation already validated (improved/resolved a matching
            # bottleneck, here or in a prior session) is used directly,
            # skipping the live arXiv round-trip entirely. A record with
            # only failed attempts (negative confidence) is not surfaced,
            # so the loop naturally re-searches instead of repeating a
            # known-bad fix.
            mem_matches = _memory_retrieve(metric, sys_context, k=1)
            if mem_matches and mem_matches[0].get("successes", 0) > mem_matches[0].get("failures", 0):
                m = mem_matches[0]
                cite = m.get("citation", {})
                found_papers.append({
                    "title":     cite.get("title", m.get("strategy_title", "")),
                    "authors":   cite.get("authors", []),
                    "published": f"{cite.get('year','')}-01-01" if cite.get("year") else "",
                    "abstract":  f"Retrieved from project memory: used {m.get('uses',0)}x, "
                                 f"{m.get('successes',0)} success(es), {m.get('failures',0)} failure(s).",
                    "url":       cite.get("url", ""),
                    "query":     "<memory>",
                    "bottleneck": metric,
                    "source":    "memory",
                })
            else:
                queries = _bottleneck_search_queries(
                    metric=metric,
                    description=description,
                    sys_context=sys_context,
                    max_queries=max_attempts,
                )
                for q in queries:
                    queries_tried.append(q)
                    papers = _fetch_arxiv_papers(q, n=n_papers)
                    for p in papers:
                        title_key = p["title"].lower()[:80]
                        if title_key not in seen_titles:
                            seen_titles.add(title_key)
                            found_papers.append({**p, "query": q, "bottleneck": metric, "source": "arxiv"})
                    if found_papers:
                        break  # stop as soon as at least one paper is found
                # Record the attempt in Tier-2 memory (outcome unknown yet —
                # session_optimization_iteration's next call reflects on it).
                if found_papers:
                    top = found_papers[0]
                    _memory_write(
                        metric=metric,
                        sys_context=sys_context,
                        strategy_title=top.get("title", "")[:120],
                        citation={
                            "authors": top.get("authors", []),
                            "title":   top.get("title", ""),
                            "venue":   f"arXiv {(top.get('published') or '')[:4]}",
                            "year":    (top.get("published") or "")[:4],
                            "url":     top.get("url", ""),
                        },
                        source="searched",
                    )

            entry = {
                "bottleneck":    metric,
                "severity":      severity,
                "description":   description,
                "sys_context":   sys_context,
                "queries_tried": len(queries_tried),
                "last_query":    queries_tried[-1] if queries_tried else "",
                "papers":        found_papers,
            }
            if found_papers:
                literature.append(entry)
            else:
                unsolved.append({
                    "bottleneck":    metric,
                    "severity":      severity,
                    "description":   description,
                    "queries_tried": queries_tried,
                    "message":       (
                        f"No papers found for '{metric}' ({severity}) after "
                        f"{len(queries_tried)} search attempt(s) including "
                        f"system-context '{sys_context}'. "
                        "Manual expert guidance recommended."
                    ),
                })

        # ── Step 7: compare with previous iteration ──────────────────────────
        prev_bottlenecks: list = []
        if history:
            prev_bottlenecks = history[-1].get("bottlenecks", [])

        def _bn_key(b: dict) -> str:
            return f"{b.get('metric', '')}:{b.get('view', '')}"

        prev_keys = {_bn_key(b): b for b in prev_bottlenecks}
        curr_keys = {_bn_key(b): b for b in current_bottlenecks}

        improved   = []
        regressed  = []
        unchanged  = []
        new_issues = []

        for key, cb in curr_keys.items():
            if key not in prev_keys:
                new_issues.append(cb)
            else:
                pb = prev_keys[key]
                cs = _SEV.get(cb.get("severity", "trivial"), 0)
                ps = _SEV.get(pb.get("severity", "trivial"), 0)
                if cs < ps:
                    improved.append({"metric": key, "from": pb["severity"], "to": cb["severity"]})
                elif cs > ps:
                    regressed.append({"metric": key, "from": pb["severity"], "to": cb["severity"]})
                else:
                    unchanged.append(key)

        resolved = [k for k in prev_keys if k not in curr_keys]

        delta = {
            "improved":  improved,
            "regressed": regressed,
            "resolved":  resolved,
            "new":       new_issues,
            "unchanged": unchanged,
        }

        # ── Build recommendation ─────────────────────────────────────────────
        # ── Build per-bottleneck citations list ──────────────────────────────
        # Each entry: {metric, severity, papers: [{title, url, authors, published}]}
        citations: List[Dict[str, Any]] = []
        for lit_entry in literature:
            cite_papers = [
                {
                    "title":     p.get("title", ""),
                    "url":       p.get("url", ""),
                    "authors":   p.get("authors", []),
                    "published": p.get("published", ""),
                    "abstract":  p.get("abstract", "")[:300] + "…" if p.get("abstract") else "",
                    "query":     p.get("query", ""),
                }
                for p in lit_entry.get("papers", [])
            ]
            citations.append({
                "metric":   lit_entry["bottleneck"],
                "severity": lit_entry["severity"],
                "papers":   cite_papers,
            })

        def _cite_str(lit_entry: dict) -> str:
            """One-line citation: 'Title (Authors, Year) <url>'"""
            papers = lit_entry.get("papers", [])
            if not papers:
                return "(no papers found)"
            p = papers[0]
            year  = (p.get("published") or "")[:4]
            auth  = p.get("authors", [])
            first = auth[0].split()[-1] if auth else "Unknown"
            et_al = " et al." if len(auth) > 1 else ""
            return (
                f'"{p["title"][:70]}..." '
                f"- {first}{et_al}, {year} "
                f"<{p.get('url', '')}>"
            )

        n_papers = sum(len(e["papers"]) for e in literature)
        lit_summary = (
            f"{n_papers} paper(s) found across {len(literature)} bottleneck(s)"
        ) if literature else "no papers found"

        if not current_bottlenecks:
            recommendation = "No active bottlenecks — optimization complete."
        elif unsolved:
            names = ", ".join(u["bottleneck"] for u in unsolved)
            recommendation = (
                f"Literature search exhausted for: {names}. "
                "These require manual expert analysis — see 'unsolved' for the "
                "full list of attempted queries. "
            )
            if literature:
                top_lit = literature[0]
                recommendation += (
                    f"For remaining solvable bottlenecks, start with "
                    f"'{top_lit['bottleneck']}': {_cite_str(top_lit)}"
                )
        elif regressed:
            cite_lines = "\n".join(f"  • [{c['metric']}] {_cite_str(c)}" for c in citations[:3])
            recommendation = (
                f"{len(regressed)} metric(s) regressed — revert last change or "
                "investigate interaction effects.\n"
                f"Evidence ({lit_summary}):\n{cite_lines}"
            )
        elif improved or resolved:
            top = top5[0] if top5 else {}
            cite_lines = "\n".join(f"  • [{c['metric']}] {_cite_str(c)}" for c in citations[:3])
            recommendation = (
                f"Progress: {len(improved)} improved, {len(resolved)} resolved. "
                f"Top remaining: {top.get('metric','')} ({top.get('severity','')}).\n"
                f"Evidence ({lit_summary}):\n{cite_lines}\n"
                "Apply the suggested optimization and iterate."
            )
        else:
            top = top5[0] if top5 else {}
            cite_lines = "\n".join(f"  • [{c['metric']}] {_cite_str(c)}" for c in citations[:3])
            recommendation = (
                f"No change yet. Top bottleneck: {top.get('metric','')} "
                f"({top.get('severity','')}).\n"
                f"Evidence ({lit_summary}):\n{cite_lines}\n"
                "Apply the technique from the first cited paper and re-run."
            )

        # ── Persist iteration to history ─────────────────────────────────────
        entry = {
            "iteration":       iteration,
            "optimization":    optimization_applied,
            "build":           build_summary,
            "profile":         profile_summary,
            "diagnosis":       diag_summary,
            "system_context":  sys_context,
            "bottlenecks":     current_bottlenecks,
            "top5":            top5,
            "literature":      literature,
            "unsolved":        unsolved,
            "delta":           delta,
            "comparison":      comparison,
        }
        history.append(entry)
        _save_state(run_id, {"optimization_history": history, "step": "optimization_loop"})

        # ── Agentic RAG: reflect — write back whether the *previous*
        # iteration's cited fix actually worked, now that this iteration's
        # delta makes that observable. No-op on the baseline (iteration 0).
        memory_reflection = _memory_reflect(run_id, iteration=iteration) if iteration > 0 else \
            {"written": 0, "detail": "baseline iteration — nothing to reflect on yet"}

        # Save full literature results to workspace for reference
        lit_file = ws / f"optimization_literature_iter{iteration}.json"
        iter_summary = {
            "iteration":    iteration,
            "optimization": optimization_applied,
            "sys_context":  sys_context,
            "literature":   literature,
            "citations":    citations,
            "unsolved":     unsolved,
            "bottlenecks":  current_bottlenecks,
            "delta":        delta,
            "comparison":   comparison,
        }
        lit_file.write_text(_json.dumps(iter_summary, indent=2))
        # Mirror into per-iteration directory for comparison
        (iter_dir / "summary.json").write_text(_json.dumps(iter_summary, indent=2))
        if sys_cfg_path.exists():
            _sh.copy2(str(sys_cfg_path), str(iter_dir / "system_config.json"))

        return _ok(
            f"Iteration {iteration} complete — {len(current_bottlenecks)} active "
            f"bottleneck(s), {len(literature)} solved by literature, "
            f"{len(unsolved)} unsolved. " + recommendation,
            iteration=iteration,
            optimization=optimization_applied,
            build=build_summary,
            profile=profile_summary,
            diagnosis=diag_summary,
            system_context=sys_context,
            top5_bottlenecks=top5,
            literature=literature,
            unsolved=unsolved,
            delta=delta,
            literature_file=str(lit_file),
            iter_dir=str(iter_dir),
            citations=citations,
            recommendation=recommendation,
            memory_reflection=memory_reflection,
        )

    @mcp.tool()
    def session_generate_optimization_proposals(
        run_id: str,
        iteration: int = -1,
        levels: str = "123",
        metric: str = "time",
        max_proposals_per_level: int = 3,
        bottlenecks_json: Optional[str] = None,
    ) -> str:
        """Generate concrete, citation-backed optimization proposals from bottleneck diagnosis.

        Reads the bottleneck list and searched literature from a completed
        ``session_optimization_iteration`` call, maps each bottleneck to a set of
        concrete code / config / system changes using the three-level strategy
        tables (L1 application, L2 software/middleware, L3 system/filesystem),
        and attaches a verifiable citation (URL) to every proposal.

        Bottlenecks are processed in the canonical optimization order
        I/O -> communication -> memory -> compute (severity is the tiebreaker
        within a category), so proposal ``id`` ordering reflects that pipeline.

        Citation priority per proposal:
        1. Papers found by the arXiv / Semantic Scholar search in the latest iteration.
        2. Built-in reference matched to the bottleneck's category: I/O ->
           WisIO (Yildirim et al., ICS 2025) or Drishti (Bez et al., PDSW 2022);
           communication -> Thakur et al. (IJHPCA 2005); memory -> McCalpin
           STREAM (1995); compute -> Williams et al. Roofline (CACM 2009).
           The two always-on DL dimensions use Mohan et al. (VLDB 2021) for
           dataloader/epoch stalls and Lockwood et al. (SC 2018) for
           filesystem bandwidth/utilization.

        A proposal is OMITTED if neither a searched paper nor a built-in reference
        can be matched to the bottleneck + strategy combination.

        Args:
            run_id:                  Session identifier.
            iteration:               Which optimization iteration to read (-1 = latest).
            levels:                  Which levels to generate proposals for.
                                     Any combination of "1", "2", "3" (default "123").
            metric:                  Optimization objective used to rank proposals
                                     (time | bandwidth | iops | metadata_ops).
            max_proposals_per_level: Cap on proposals returned per level (default 3).

        Returns:
            JSON with keys:
            * ``status``     — ``"ok"`` or ``"error"``.
            * ``proposals``  — list of proposal dicts, each containing:
              ``id``, ``level``, ``title``, ``bottleneck``, ``severity``,
              ``change``, ``expected_delta``, ``risk``, ``citation``
              (sub-keys: authors, title, venue, year, url, finding),
              and for L2: ``delivery``, ``env_key``;
              for L3: ``privilege``, ``rollback``, ``side_effect``.
            * ``unsupported`` — bottlenecks for which no strategy was found.
            * ``citation_sources`` — breakdown of how many proposals used searched
              papers vs built-in references.

        Args:
            bottlenecks_json: Optional JSON list of bottleneck dicts, used when the
                run was launched externally (e.g. a `flux batch` job) and so has no
                ``optimization_history``. Falls back to
                ``<ws>/<run>/analysis/diagnosis.json`` when omitted.
        """
        import json as _json

        state = _load_state(run_id)
        history: list = state.get("optimization_history", [])

        if not history:
            # session_optimization_iteration assumes a locally-launched run. Runs
            # submitted externally (e.g. a two-phase `flux batch` job on 8 nodes)
            # never populate optimization_history, yet they have a perfectly good
            # diagnosis. Accept one directly rather than dead-ending the optimizer.
            fallback = _load_external_bottlenecks(run_id, bottlenecks_json)
            if fallback is None:
                return _err(
                    "No optimization iterations found. Either run "
                    "session_optimization_iteration first, or pass bottlenecks_json "
                    "(a JSON list of {bottleneck, severity, category, ...}), or write "
                    "<ws>/baseline/analysis/diagnosis.json."
                )
            bottlenecks, literature = fallback, []
        else:
            idx = iteration if iteration >= 0 else len(history) - 1
            if idx >= len(history):
                return _err(f"Iteration {iteration} not found (history has {len(history)} entries).")

            iter_entry  = history[idx]
            bottlenecks = iter_entry.get("bottlenecks", [])
            literature  = iter_entry.get("literature", [])

        # Build metric → papers lookup from the iteration's existing literature search
        searched: Dict[str, List[Dict[str, Any]]] = {}
        for lit_entry in literature:
            m = lit_entry.get("bottleneck", "")
            searched[m] = lit_entry.get("papers", [])

        want_l1 = "1" in levels
        want_l2 = "2" in levels
        want_l3 = "3" in levels

        all_proposals: List[Dict[str, Any]] = []
        all_unsupported: List[str] = []
        total_cs = 0
        total_cb = 0

        if want_l1:
            p1, cs1, cb1 = _gen_level_proposals(
                bottlenecks, _L1_STRATEGIES, "L1", searched,
                max_per_level=max_proposals_per_level,
            )
            all_proposals.extend(p1)
            total_cs += cs1
            total_cb += cb1

        if want_l2:
            p2, cs2, cb2 = _gen_level_proposals(
                bottlenecks, _L2_STRATEGIES, "L2", searched,
                max_per_level=max_proposals_per_level,
                extra_fields=["delivery", "env_key"],
            )
            all_proposals.extend(p2)
            total_cs += cs2
            total_cb += cb2

        if want_l3:
            p3, cs3, cb3 = _gen_level_proposals(
                bottlenecks, _L3_STRATEGIES, "L3", searched,
                max_per_level=max_proposals_per_level,
                extra_fields=["privilege", "rollback", "side_effect"],
            )
            all_proposals.extend(p3)
            total_cs += cs3
            total_cb += cb3

        # Collect bottlenecks not covered by any level
        _SEV = {"trivial": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        covered = {p["bottleneck"] for p in all_proposals}
        for bn in bottlenecks:
            met = bn.get("metric", "")
            if _SEV.get(bn.get("severity", "trivial"), 0) >= 2 and met not in covered:
                all_unsupported.append(met)

        total = len(all_proposals)
        return _ok(
            f"{total} citation-backed proposal(s) across levels '{levels}' "
            f"for run {run_id} iteration {idx}. "
            f"Citations: {total_cs} from arXiv search, {total_cb} from built-in references. "
            f"Unsupported bottlenecks: {all_unsupported or 'none'}.",
            proposals=all_proposals,
            unsupported=all_unsupported,
            citation_sources={
                "searched_papers": total_cs,
                "builtin_references": total_cb,
            },
            iteration=idx,
        )

    @mcp.tool()
    def session_optimize_l1_app(
        run_id: str,
        iteration: int = -1,
        metric: str = "time",
        max_proposals: int = 5,
    ) -> str:
        """Generate citation-backed application-code optimization proposals (Level 1).

        Searches arXiv for papers on application-level I/O optimization techniques
        that target the detected bottlenecks (buffer coalescing, async I/O, access
        reordering, DataLoader tuning, checkpoint async-write, etc.).

        Every proposal MUST be backed by a verifiable citation (URL).
        Proposals without a citation are silently dropped.

        Args:
            run_id:         Session identifier.
            iteration:      Which optimization iteration to read (-1 = latest).
            metric:         Optimization objective (time | bandwidth | iops | metadata_ops).
            max_proposals:  Maximum total proposals to return (default 5).

        Returns:
            JSON with keys: status, proposals (list with citation sub-key per entry),
            citation_sources (searched vs built-in counts), unsupported (unmatched metrics).
        """
        import json as _json

        state   = _load_state(run_id)
        history = state.get("optimization_history", [])
        if not history:
            return _err("No optimization iterations — run session_optimization_iteration first.")

        idx  = iteration if iteration >= 0 else len(history) - 1
        iter_e = history[idx]
        bottlenecks = iter_e.get("bottlenecks", [])

        # ── Targeted arXiv searches per unique bottleneck ────────────────────
        _SEV = {"trivial": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        high_bns = [b for b in bottlenecks if _SEV.get(b.get("severity","trivial"),0) >= 2]
        searched: Dict[str, List[Dict[str, Any]]] = {}

        # Merge already-searched papers from the iteration
        for lit in iter_e.get("literature", []):
            searched[lit["bottleneck"]] = lit.get("papers", [])

        # Domain-specific L1 search queries per bottleneck
        _L1_SEARCH: Dict[str, List[str]] = {
            "small_io":       ["application buffer coalescing small I/O HPC performance",
                               "buffered I/O aggregation parallel file access"],
            "small_read":     ["read buffering application layer HPC I/O optimization"],
            "small_write":    ["write buffering batch I/O application code parallel"],
            "rand_pct":       ["random to sequential I/O reordering application optimization HPC",
                               "index sorting access pattern parallel I/O performance"],
            "seq_pct":        ["sequential access optimization posix_fadvise prefetch HPC"],
            "read_time":      ["async I/O io_uring application code latency HPC",
                               "non-blocking I/O file descriptor pre-open parallel"],
            "write_time":     ["async checkpoint write background thread HPC application",
                               "fallocate write performance parallel storage"],
            "metadata_time":  ["metadata caching application layer stat syscall HPC",
                               "file open reuse epoch training metadata optimization"],
            "fetch_pressure": ["DataLoader parallel I/O workers prefetch deep learning HPC",
                               "data pipeline prefetching GPU training throughput"],
            "epoch_straggler":["sample size bucketing training batch latency optimization",
                               "straggler mitigation deep learning data loading"],
        }

        seen_titles: set = set()
        for bn in high_bns:
            met = bn.get("metric", "")
            if met in searched and searched[met]:
                continue  # already have papers for this metric
            queries = _L1_SEARCH.get(met, [
                f"application code {met} optimization HPC parallel I/O",
                f"{met} I/O performance tuning source code",
            ])
            for q in queries:
                papers = _fetch_arxiv_papers(q, n=3)
                unique = [p for p in papers
                          if p.get("title","").lower()[:60] not in seen_titles]
                if unique:
                    for p in unique:
                        seen_titles.add(p.get("title","").lower()[:60])
                    searched[met] = unique
                    break

        proposals, cs, cb = _gen_level_proposals(
            bottlenecks, _L1_STRATEGIES, "L1",
            searched, max_per_level=max_proposals,
        )
        unsupported = [b["metric"] for b in high_bns
                       if not any(p["bottleneck"] == b["metric"] for p in proposals)]

        return _ok(
            f"L1 app: {len(proposals)} citation-backed proposal(s). "
            f"Citations: {cs} arXiv, {cb} built-in. Unsupported: {unsupported or 'none'}.",
            proposals=proposals,
            unsupported=unsupported,
            citation_sources={"searched_papers": cs, "builtin_references": cb},
            level="L1",
            iteration=idx,
        )

    @mcp.tool()
    def session_optimize_l2_software(
        run_id: str,
        iteration: int = -1,
        metric: str = "time",
        max_proposals: int = 5,
    ) -> str:
        """Generate citation-backed software/middleware optimization proposals (Level 2).

        Searches arXiv for papers on MPI-IO collective buffering, ROMIO hints,
        HDF5 chunk/cache tuning, PyTorch DataLoader env-var tuning, NetCDF/PnetCDF
        settings, and other middleware configuration changes that do not require
        source code edits.

        Every proposal is backed by a verifiable citation (URL).

        Args:
            run_id:         Session identifier.
            iteration:      Which optimization iteration to read (-1 = latest).
            metric:         Optimization objective (time | bandwidth | iops | metadata_ops).
            max_proposals:  Maximum total proposals to return (default 5).

        Returns:
            JSON with keys: status, proposals (each with citation + delivery/env_key),
            citation_sources, unsupported.
        """
        import json as _json

        state   = _load_state(run_id)
        history = state.get("optimization_history", [])
        if not history:
            return _err("No optimization iterations — run session_optimization_iteration first.")

        idx    = iteration if iteration >= 0 else len(history) - 1
        iter_e = history[idx]
        bottlenecks = iter_e.get("bottlenecks", [])

        _SEV = {"trivial": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        high_bns = [b for b in bottlenecks if _SEV.get(b.get("severity","trivial"),0) >= 2]

        searched: Dict[str, List[Dict[str, Any]]] = {}
        for lit in iter_e.get("literature", []):
            searched[lit["bottleneck"]] = lit.get("papers", [])

        _L2_SEARCH: Dict[str, List[str]] = {
            "small_io":       ["ROMIO collective buffering MPI-IO small request optimization",
                               "MPI-IO cb_buffer_size collective I/O aggregation performance"],
            "rand_pct":       ["ROMIO data sieving non-contiguous MPI-IO access",
                               "MPI-IO hints access pattern optimization parallel"],
            "read_time":      ["ROMIO collective read buffering MPI-IO latency reduction",
                               "HDF5 chunk cache read performance parallel HPC"],
            "write_time":     ["ROMIO collective write MPI-IO throughput hints",
                               "HDF5 collective metadata write parallel performance"],
            "metadata_time":  ["HDF5 metadata cache collective write parallel optimization",
                               "MPI-IO metadata overhead reduction hints"],
            "fetch_pressure": ["PyTorch DataLoader num_workers prefetch_factor throughput",
                               "deep learning data pipeline I/O throughput GPU training"],
            "epoch_straggler":["PyTorch DistributedSampler persistent_workers optimization",
                               "deep learning training epoch tail latency worker tuning"],
        }

        seen_titles: set = set()
        for bn in high_bns:
            met = bn.get("metric","")
            if met in searched and searched[met]:
                continue
            queries = _L2_SEARCH.get(met, [
                f"middleware configuration {met} parallel I/O optimization",
                f"MPI-IO HDF5 {met} tuning HPC performance",
            ])
            for q in queries:
                papers = _fetch_arxiv_papers(q, n=3)
                unique = [p for p in papers
                          if p.get("title","").lower()[:60] not in seen_titles]
                if unique:
                    for p in unique:
                        seen_titles.add(p.get("title","").lower()[:60])
                    searched[met] = unique
                    break

        proposals, cs, cb = _gen_level_proposals(
            bottlenecks, _L2_STRATEGIES, "L2",
            searched, max_per_level=max_proposals,
            extra_fields=["delivery", "env_key"],
        )
        unsupported = [b["metric"] for b in high_bns
                       if not any(p["bottleneck"] == b["metric"] for p in proposals)]

        return _ok(
            f"L2 software: {len(proposals)} citation-backed proposal(s). "
            f"Citations: {cs} arXiv, {cb} built-in. Unsupported: {unsupported or 'none'}.",
            proposals=proposals,
            unsupported=unsupported,
            citation_sources={"searched_papers": cs, "builtin_references": cb},
            level="L2",
            iteration=idx,
        )

    @mcp.tool()
    def session_optimize_l3_filesystem(
        run_id: str,
        iteration: int = -1,
        metric: str = "time",
        max_proposals: int = 5,
    ) -> str:
        """Generate citation-backed filesystem/OS optimization proposals (Level 3).

        Searches arXiv for papers on Lustre stripe tuning, GPFS configuration,
        Linux kernel readahead (blockdev --setra), vm.dirty page writeback tuning,
        I/O scheduler selection, and NUMA memory binding for I/O-bound workloads.

        Every proposal includes:
        - A verifiable citation URL (arXiv or built-in reference)
        - Required privilege level (no-sudo | sudo | admin-only)
        - Rollback command
        - Side-effect warning

        Args:
            run_id:         Session identifier.
            iteration:      Which optimization iteration to read (-1 = latest).
            metric:         Optimization objective (time | bandwidth | iops | metadata_ops).
            max_proposals:  Maximum total proposals to return (default 5).

        Returns:
            JSON with keys: status, proposals (each with citation + privilege/rollback/side_effect),
            citation_sources, unsupported.
        """
        import json as _json

        state   = _load_state(run_id)
        history = state.get("optimization_history", [])
        if not history:
            return _err("No optimization iterations — run session_optimization_iteration first.")

        idx    = iteration if iteration >= 0 else len(history) - 1
        iter_e = history[idx]
        bottlenecks = iter_e.get("bottlenecks", [])

        _SEV = {"trivial": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        high_bns = [b for b in bottlenecks if _SEV.get(b.get("severity","trivial"),0) >= 2]

        searched: Dict[str, List[Dict[str, Any]]] = {}
        for lit in iter_e.get("literature", []):
            searched[lit["bottleneck"]] = lit.get("papers", [])

        _L3_SEARCH: Dict[str, List[str]] = {
            "small_io":       ["Lustre striping small file I/O optimization HPC",
                               "parallel filesystem stripe configuration performance"],
            "rand_pct":       ["Linux kernel readahead blockdev setra optimization HPC",
                               "sequential I/O readahead tuning parallel workload"],
            "read_time":      ["page cache pressure vfs_cache_pressure HPC read optimization",
                               "Linux kernel I/O performance read cache tuning"],
            "write_time":     ["vm.dirty_ratio dirty page writeback HPC I/O tuning",
                               "Linux kernel write performance dirty page flush optimization"],
            "metadata_time":  ["Lustre metadata striping DNE directory optimization",
                               "parallel filesystem metadata performance tuning HPC"],
            "fetch_pressure": ["NUMA memory binding I/O performance HPC training",
                               "numactl memory locality deep learning GPU I/O throughput"],
        }

        seen_titles: set = set()
        for bn in high_bns:
            met = bn.get("metric","")
            if met in searched and searched[met]:
                continue
            queries = _L3_SEARCH.get(met, [
                f"filesystem OS {met} optimization HPC parallel",
                f"Lustre GPFS {met} tuning performance",
            ])
            for q in queries:
                papers = _fetch_arxiv_papers(q, n=3)
                unique = [p for p in papers
                          if p.get("title","").lower()[:60] not in seen_titles]
                if unique:
                    for p in unique:
                        seen_titles.add(p.get("title","").lower()[:60])
                    searched[met] = unique
                    break

        proposals, cs, cb = _gen_level_proposals(
            bottlenecks, _L3_STRATEGIES, "L3",
            searched, max_per_level=max_proposals,
            extra_fields=["privilege", "rollback", "side_effect"],
        )
        unsupported = [b["metric"] for b in high_bns
                       if not any(p["bottleneck"] == b["metric"] for p in proposals)]

        return _ok(
            f"L3 filesystem: {len(proposals)} citation-backed proposal(s). "
            f"Citations: {cs} arXiv, {cb} built-in. Unsupported: {unsupported or 'none'}.",
            proposals=proposals,
            unsupported=unsupported,
            citation_sources={"searched_papers": cs, "builtin_references": cb},
            level="L3",
            iteration=idx,
        )

    @mcp.tool()
    def session_snapshot_l1_source(
        run_id: str,
        iteration: int,
        label: str = "",
    ) -> str:
        """Snapshot the current annotated source tree before or after an L1 optimization pass.

        Creates a versioned copy of ``<workspace>/annotated/`` at
        ``<workspace>/opt_snapshots/l1_iter_<N>/source/`` so that application-code
        changes made during L1 optimization are permanently recorded and comparable
        across iterations.

        Call this tool:
        * **Before** applying any accepted L1 proposal (``iteration=0`` for the
          pre-optimization baseline, ``iteration=N`` before each subsequent round).
        * The snapshot directory is never overwritten — if it already exists the
          tool returns an error so callers know they need to bump the iteration
          number.

        Directory layout written::

            <workspace>/opt_snapshots/
              l1_iter_0/         ← baseline (before any L1 changes)
                source/          ← copy of annotated/ at this point
                snapshot.json    ← metadata: timestamp, label, session state excerpt
              l1_iter_1/         ← after first accepted proposal batch
                source/
                snapshot.json
              l1_iter_2/ ...

        Side effects:
            * Creates ``<workspace>/opt_snapshots/l1_iter_<N>/source/`` with a
              recursive copy of ``<workspace>/annotated/``.
            * Writes ``snapshot.json`` with timestamp, label, and a summary of
              the current ``session.json`` state.
            * Persists ``{"l1_snapshot_<N>": "<path>"}`` to ``session.json``.

        Args:
            run_id:    Session identifier.
            iteration: Iteration index (0 = baseline, 1 = first proposal batch, …).
            label:     Optional human-readable description of what changed
                       (e.g. ``"applied buffer coalescing"``).

        Returns:
            JSON with keys: ``status``, ``snapshot_dir``, ``iteration``, ``label``,
            ``files_copied``.
        """
        import datetime as _dt

        ws = _ws(run_id)
        ann_dir = ws / "annotated"
        if not ann_dir.exists():
            return _err(
                "annotated/ not found — run session_copy_annotated first.",
                run_id=run_id,
            )

        snap_root = ws / "opt_snapshots" / f"l1_iter_{iteration}"
        snap_src  = snap_root / "source"

        if snap_root.exists():
            return _err(
                f"Snapshot for iteration {iteration} already exists at {snap_root}. "
                "Increment iteration to create a new snapshot.",
                snapshot_dir=str(snap_root),
            )

        shutil.copytree(ann_dir, snap_src, symlinks=True, ignore_dangling_symlinks=True)

        files_copied = sum(1 for _ in snap_src.rglob("*") if _.is_file())

        state = _load_state(run_id)
        snapshot_meta = {
            "iteration":    iteration,
            "label":        label or f"l1_iter_{iteration}",
            "timestamp":    _dt.datetime.utcnow().isoformat() + "Z",
            "source_dir":   str(ann_dir),
            "snapshot_dir": str(snap_src),
            "files_copied": files_copied,
            "session_step": state.get("step", ""),
        }
        (snap_root / "snapshot.json").write_text(json.dumps(snapshot_meta, indent=2))

        _save_state(run_id, {f"l1_snapshot_{iteration}": str(snap_root)})

        return _ok(
            f"Snapshot {iteration} created: {files_copied} file(s) copied to {snap_src}",
            snapshot_dir=str(snap_root),
            source_copy=str(snap_src),
            iteration=iteration,
            label=label or f"l1_iter_{iteration}",
            files_copied=files_copied,
        )

    @mcp.tool()
    def session_run_l1_iteration(
        run_id: str,
        iteration: int,
        command: str,
        subfolder: str = "build_ann",
        data_dir: str = "all",
        timeout: int = 600,
        env_extra: str = "",
        app_name: str = "app",
    ) -> str:
        """Run the benchmark with dftracer and collect iteration-specific L1 traces.

        Behaves like ``session_run_with_dftracer`` but writes traces into a
        per-iteration directory so that multiple optimization rounds remain
        independently analyzable:

        * Traces written to  ``<workspace>/traces_opt_l1_iter_<N>/``
        * Split output goes to ``<workspace>/traces_opt_l1_iter_<N>_split/``
        * Persists the run result and trace paths under
          ``l1_iterations[N]`` in ``session.json``.

        Typical L1 iteration workflow::

            session_snapshot_l1_source(run_id, iteration=N)   # snapshot source first
            # ... agent applies accepted proposals to annotated/ ...
            session_build_annotated(run_id)                   # rebuild
            session_run_l1_iteration(run_id, iteration=N, command="./run.sh")
            session_analyze_traces(run_id,
                trace_subdir=f"traces_opt_l1_iter_{N}_split")
            session_diagnose_bottlenecks(run_id)              # compare vs baseline

        Args:
            run_id:     Session identifier.
            iteration:  L1 iteration index (must match a prior ``session_snapshot_l1_source``
                        call — enforced by convention, not hard-checked).
            command:    Shell command to run the benchmark (same as ``session_run_with_dftracer``).
            subfolder:  Working directory relative to workspace root.
                        Defaults to ``"build_ann"``.
            data_dir:   Value for ``DFTRACER_DATA_DIR``.  Defaults to ``"all"``
                        (trace all I/O paths).
            timeout:    Seconds before the subprocess is killed.  Defaults to ``600``.
            env_extra:  Optional JSON object string with extra environment variables
                        to set alongside the DFTRACER_* ones.
            app_name:   Prefix for split output chunk files.  Defaults to ``"app"``.

        Returns:
            JSON with keys: ``status``, ``message``, ``iteration``, ``traces_dir``,
            ``split_dir``, plus the subprocess result fields (``stdout``, ``stderr``,
            ``returncode``).
        """
        ws = _ws(run_id)

        traces_dir = ws / f"traces_opt_l1_iter_{iteration}"
        split_dir  = ws / f"traces_opt_l1_iter_{iteration}_split"
        traces_dir.mkdir(exist_ok=True)
        split_dir.mkdir(exist_ok=True)

        cwd = ws / subfolder
        if not cwd.exists():
            cwd = ws / "build"
        if not cwd.exists():
            cwd = ws / "source"

        run_id_safe = run_id.replace("/", "_")
        log_prefix = str(traces_dir / run_id_safe)
        Path(log_prefix).parent.mkdir(parents=True, exist_ok=True)

        env: Dict[str, str] = {
            "DFTRACER_ENABLE":       "1",
            "DFTRACER_INC_METADATA": "1",
            "DFTRACER_LOG_FILE":     log_prefix,
            "DFTRACER_DATA_DIR":     data_dir,
        }
        if env_extra:
            env.update(json.loads(env_extra))

        r = _run(["/bin/sh", "-c", command], cwd=cwd, env=env, timeout=timeout)

        # Persist run result before attempting split (split failure is non-fatal)
        iter_record: Dict[str, Any] = {
            "iteration":  iteration,
            "command":    command,
            "traces_dir": str(traces_dir),
            "split_dir":  str(split_dir),
            "run_result": r,
        }

        # Auto-split into iter-specific split dir
        trace_files = (
            list(traces_dir.rglob("*.pfw"))
            + list(traces_dir.rglob("*.pfw.gz"))
        )
        split_result: Dict[str, Any] = {}
        _SPLIT_MB = 512
        if not trace_files:
            split_result = {"success": False, "stdout": "No .pfw/.pfw.gz files found in traces dir"}
        elif len(trace_files) == 1:
            uncompressed = _dftracer_info_uncompressed_bytes(str(trace_files[0]))
            if uncompressed is not None and uncompressed <= _SPLIT_MB * 1024 * 1024:
                shutil.copy2(trace_files[0], split_dir / trace_files[0].name)
                split_result = {
                    "success": True,
                    "stdout": f"Single file ({uncompressed / (1024*1024):.1f} MB) copied without splitting",
                }
            else:
                split_result = _dftracer_utils_split(
                    directory=str(traces_dir),
                    output_dir=str(split_dir),
                    app_name=app_name,
                )
        else:
            split_result = _dftracer_utils_split(
                directory=str(traces_dir),
                output_dir=str(split_dir),
                app_name=app_name,
            )

        iter_record["split_result"] = split_result

        # Update session state: append to l1_iterations list
        state = _load_state(run_id)
        iters = state.get("l1_iterations", [])
        # Replace entry if iteration index already recorded
        iters = [e for e in iters if e.get("iteration") != iteration]
        iters.append(iter_record)
        iters.sort(key=lambda x: x.get("iteration", 0))
        _save_state(run_id, {"l1_iterations": iters})
        _write_artifact_log(
            ws, 20 + iteration, "session_run_l1_iteration",
            {"iteration": iteration, "result": r},
            run_id,
        )

        if r["success"]:
            return _ok(
                f"L1 iteration {iteration} run complete. "
                f"Traces: {traces_dir}. Split: {split_dir}.",
                iteration=iteration,
                traces_dir=str(traces_dir),
                split_dir=str(split_dir),
                **r,
            )
        return _err(
            f"L1 iteration {iteration} command failed.",
            iteration=iteration,
            traces_dir=str(traces_dir),
            split_dir=str(split_dir),
            **r,
        )


    @mcp.tool()
    def dftracer_optimize_check(proposals_json: str = "", comparison_json: str = "", stage: str = "table") -> str:
        """Deterministically validate optimization proposals and a before/after comparison; never applies anything.

        Use after session_generate_optimization_proposals (stage="generated") or after building the final
        opt_proposal_table (stage="table", the default), and after each measured iteration. proposals_json is a
        JSON list or an object with proposals/accepted/rows (or a path to one). comparison_json is
        {"baseline": {"run_id", "metric", "value"}, "variant": {...}} (variant may be called optimized).
        Checks: proposals present; every proposal has level L1/L2/L3 and a usable citation; none is "doing less";
        for the table: numeric app/system impact, a valid status, a skip_reason for not-applied/deferred rows, and a
        measured actual_app_impact_pct for applied rows; a comparison names both runs, distinct, same metric, both
        values measured.

        Returns JSON with keys: status ("pass" | "fail" | "inconclusive" when nothing was given), checks (check, ok,
        detail, fix), failed.
        """
        return _optimize_check(proposals_json, comparison_json, stage)
