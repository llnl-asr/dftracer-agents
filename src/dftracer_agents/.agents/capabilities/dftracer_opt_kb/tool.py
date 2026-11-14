"""dftracer_opt_kb: the cross-session optimization knowledge base (opt_kb_*), the proposal table, and the per-session strategy memory (session_memory_*), with a deterministic validator of KB entries (dftracer_opt_kb_check)."""
from __future__ import annotations

import json
import time
from dftracer_agents.mcp_tools.tools.optimizations.knowledge_base import _APP_REGRESSION_TOLERANCE_PCT
from dftracer_agents.mcp_tools.tools.optimizations.knowledge_base import _CITATION_RANK
from dftracer_agents.mcp_tools.tools.optimizations.knowledge_base import _LEVEL_TO_SCOPE
from dftracer_agents.mcp_tools.tools.optimizations.knowledge_base import _METRIC_SCOPES
from dftracer_agents.mcp_tools.tools.optimizations.knowledge_base import _SCOPES
from dftracer_agents.mcp_tools.tools.optimizations.knowledge_base import _SCOPE_TO_LEVEL
from dftracer_agents.mcp_tools.tools.optimizations.knowledge_base import _classify_citation
from dftracer_agents.mcp_tools.tools.optimizations.knowledge_base import _find_prior
from dftracer_agents.mcp_tools.tools.optimizations.knowledge_base import _kb_path
from dftracer_agents.mcp_tools.tools.optimizations.knowledge_base import _lookup
from dftracer_agents.mcp_tools.tools.optimizations.knowledge_base import _pct
from dftracer_agents.mcp_tools.tools.optimizations.knowledge_base import _render_rows
from dftracer_agents.mcp_tools.tools.optimizations.knowledge_base import _render_to_skill
from dftracer_agents.mcp_tools.tools.optimizations.knowledge_base import _skill_dir
from dftracer_agents.mcp_tools.tools.optimizations.memory import _load_memory
from dftracer_agents.mcp_tools.tools.optimizations.memory import _memory_reflect
from dftracer_agents.mcp_tools.tools.optimizations.memory import _memory_retrieve
from dftracer_agents.mcp_tools.tools.optimizations.memory import _memory_write
from dftracer_agents.mcp_tools.tools.session.workspace import _err
from dftracer_agents.mcp_tools.tools.session.workspace import _ok
from typing import Any
from typing import Dict
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

# --------------------------------------------------------------------------- dftracer_opt_kb_check
def _kb_entry_problems(e: dict) -> List[str]:
    from dftracer_agents.mcp_tools.tools.optimizations.knowledge_base import _classify_citation, _pct, _LEVEL_TO_SCOPE, _SCOPES, _METRIC_SCOPES
    out = []
    scope = _LEVEL_TO_SCOPE.get(str(e.get("scope", "")).upper(), str(e.get("scope", "")).lower())
    if scope not in _SCOPES:
        out.append(f"scope {e.get('scope')!r} is not workload/software/system (L1/L2/L3)")
    cit = str(e.get("citation") or "").strip()
    if not cit:
        out.append("no citation")
    elif _citation_problem(cit):
        out.append(f"citation: {_citation_problem(cit)}")
    if scope == "system" and not e.get("system"):
        out.append("system-scoped entry without system")
    if scope == "workload" and not e.get("workload"):
        out.append("workload-scoped entry without workload")
    if not str(e.get("change") or "").strip():
        out.append("no change (the exact lever)")
    if not str(e.get("metric") or "").strip():
        out.append("no metric")
    if not (_num(e.get("before")) and _num(e.get("after"))):
        out.append("before/after are not measured numbers (a proposal is not a KB entry)")
    ms = str(e.get("metric_scope") or "app").lower()
    if ms not in _METRIC_SCOPES:
        out.append(f"metric_scope {ms!r} is not app/system")
    if ms == "system" and not (str(e.get("app_metric") or "").strip() and _num(e.get("app_before")) and _num(e.get("app_after"))):
        out.append("metric_scope=system without the paired app_metric/app_before/app_after (non-degradation guard)")
    if "verdict" in e and _num(e.get("before")) and _num(e.get("after")):
        d = _pct(e["before"], e["after"], bool(e.get("lower_is_better", True)))
        want = "unknown" if d is None else "win" if d >= 5 else "regression" if d <= -5 else "no_change"
        if ms == "system" and e.get("app_verdict") == "regression":
            want = "regression"
        if e["verdict"] != want:
            out.append(f"verdict {e['verdict']!r} disagrees with the numbers ({d}% -> {want})")
    if cit.lower().startswith("session:") and e.get("run_id") and cit.split(":", 1)[1] != str(e["run_id"]):
        out.append("session: citation names another run than run_id")
    if "citation_type" in e and cit and e["citation_type"] not in ("paper", "docs", "web", "session"):
        out.append(f"citation_type {e['citation_type']!r} unknown (classifier says {_classify_citation(cit)})")
    return out


def _opt_kb_check(entry_json: str, kb_file: str) -> str:
    checks: List[dict] = []
    entries: List[dict] = []
    if entry_json.strip():
        try:
            data = _parse_json_arg(entry_json)
            entries += [data] if isinstance(data, dict) and "entries" not in data else [e for e in _rows(data) if isinstance(e, dict)]
        except ValueError as e:
            _add(checks, "entry_parse", False, str(e), "pass the entry (or a list of entries) as JSON")
    if kb_file:
        p = Path(kb_file)
        if not p.is_file():
            _add(checks, "kb_file_exists", False, f"{p} does not exist", "pass <workspaces>/_memory/optimization_kb.jsonl")
        else:
            bad_lines = []
            for n, line in enumerate(p.read_text().splitlines(), 1):
                if line.strip():
                    try:
                        entries.append(json.loads(line))
                    except ValueError:
                        bad_lines.append(n)
            _add(checks, "kb_file_parses", not bad_lines, f"unparseable lines: {bad_lines}" if bad_lines else "ok",
                 "remove or repair the corrupt lines (the lookup skips them silently)")
    if not entries and not checks:
        return _verdict([])
    probs = [f"entry {i}: {'; '.join(p)}" for i, e in enumerate(entries, 1) if (p := _kb_entry_problems(e))]
    if entries:
        _add(checks, "entries_valid", not probs, "; ".join(probs)[:2000] or f"{len(entries)} entr(y/ies) valid",
             "record only measured results with scope, change, metric, before/after and a citation (paper > docs > web > session:<run_id>)")
    return _verdict(checks, entries=len(entries))


def register(mcp):
    """Register this capability's tools on *mcp*."""

    @mcp.tool()
    def opt_kb_lookup(
        system: str = "",
        workload: str = "",
        software: str = "",
        scope: str = "",
        bottleneck: str = "",
        limit: int = 20,
        metric_scope: str = "",
    ) -> str:
        """STEP 1 of the optimization loop — what has already been tried here?

        Returns prior MEASURED optimization results, ranked by how much they
        transfer to the current context:

        * ``system`` findings are returned only for the same system;
        * ``workload`` findings only for the same application;
        * ``software`` findings travel across both.

        Call this BEFORE generating proposals, and carry the results into the
        proposal table's "prior result" column. It is how the pipeline stops
        re-deriving that Cray MPICH ignores ``cb_nodes``, or that a serial HDF5
        IO unit makes ``useCollectiveHDF5`` inert.

        Args:
            system: e.g. ``"tuolumne"``.
            workload: e.g. ``"flash_x"``.
            software: e.g. ``"hdf5"``, ``"mpi-io"``, ``"lustre"``.
            scope: ``"system"``/``"software"``/``"workload"`` (or ``L3``/``L2``/``L1``)
                — the LAYER a finding applies to (who inherits it).
            bottleneck: substring filter on the recorded bottleneck/change.
            limit: max rows.
            metric_scope: ``"app"``/``"system"`` — the orthogonal axis for WHICH
                metric moved (app trace time/bandwidth vs system-level achieved
                filesystem bandwidth/load). Leave blank to see both.

        Returns:
            JSON with ``count``, ``rows`` (each with ``delta_pct``, ``verdict``,
            ``citation``, and — for ``metric_scope="system"`` rows — the paired
            ``app_metric``/``app_delta_pct`` proving it didn't regress the app),
            and ``markdown`` — a ready-to-paste table.
        """
        rows = _lookup(system, workload, software, scope, bottleneck, limit, metric_scope)
        return _ok(f"{len(rows)} prior result(s)", count=len(rows), rows=rows,
                   markdown=_render_rows(rows))

    @mcp.tool()
    def opt_kb_record(
        scope: str,
        change: str,
        metric: str,
        before: float,
        after: float,
        citation: str,
        system: str = "",
        workload: str = "",
        software: str = "",
        bottleneck: str = "",
        lower_is_better: bool = True,
        run_id: str = "",
        notes: str = "",
        citation_type: str = "",
        metric_scope: str = "app",
        app_metric: str = "",
        app_before: Optional[float] = None,
        app_after: Optional[float] = None,
        app_lower_is_better: bool = True,
    ) -> str:
        """Record ONE measured optimization result into the cross-session KB.

        Call this after each iteration — one change, measured — so the next
        session inherits the result. Record losses and no-ops too: "cb_nodes=8 was
        accepted but changed nothing" is exactly the finding that saves the next
        run a wasted iteration.

        A citation is REQUIRED. Preference order: paper (arXiv/DOI/ACM/IEEE/USENIX)
        > official documentation > web page > ``session:<run_id>`` for an in-house
        measurement with no external source.

        Args:
            scope: ``"system"`` (L3) / ``"software"`` (L2) / ``"workload"`` (L1) —
                the LAYER this finding applies to (who inherits it).
            change: the exact lever, e.g. ``"cb_nodes=16 + CRAY_CB_NODES_MULTIPLIER=2"``.
            metric: what was measured, e.g. ``"critical-path write time (s)"``
                when ``metric_scope="app"``, or e.g. ``"aggregate achieved
                filesystem bandwidth (GB/s)"`` when ``metric_scope="system"``.
            before / after: measured values for ``metric``.
            citation: URL, DOI, or ``session:<run_id>``.
            citation_type: override the auto-classification when the URL heuristic
                misreads it (``paper``/``docs``/``web``/``session``). The heuristic
                will always have blind spots — a project page is documentation even
                when its hostname says otherwise.
            system / workload / software: context keys used for recall.
            lower_is_better: True for time/latency, False for bandwidth — applies
                to ``metric``.
            bottleneck: the bottleneck this addressed.
            run_id: session that produced the measurement.
            notes: caveats — e.g. "only effective after +parallelIO".
            metric_scope: ``"app"`` (default — a metric from the app's own trace:
                epoch/I-O time, app-observed bandwidth) or ``"system"`` (a
                filesystem/system-level outcome: aggregate achieved bandwidth,
                reduced filesystem load). This is a SEPARATE axis from ``scope``
                — an L3 (filesystem) change can still target the app's own
                metric; a ``metric_scope="system"`` entry specifically means the
                thing being measured is a system-side number, not the app's.
            app_metric / app_before / app_after / app_lower_is_better:
                **REQUIRED when metric_scope="system"**. The paired app-level
                measurement (e.g. epoch time) for the SAME change — this is how
                the KB enforces that a system-metric win is never recorded
                without proof it didn't cost the app anything. A system-scoped
                entry whose app metric regressed beyond 2% is force-verdicted
                ``"regression"`` regardless of how good the system number looks.

        Returns:
            JSON with the stored ``entry`` including ``delta_pct``/``verdict``,
            and — for ``metric_scope="system"`` — ``app_delta_pct``/``app_verdict``
            and whether the non-degradation guard fired.
        """
        scope_n = _LEVEL_TO_SCOPE.get(scope.upper(), scope.lower())
        if scope_n not in _SCOPES:
            return _err(f"scope must be one of {_SCOPES} (or L1/L2/L3), got {scope!r}")
        if not citation.strip():
            return _err("citation is required: paper > docs > web > session:<run_id>")
        if scope_n == "system" and not system:
            return _err("system-scoped entries require `system` (they do not transfer off it)")
        if scope_n == "workload" and not workload:
            return _err("workload-scoped entries require `workload`")

        metric_scope_n = metric_scope.strip().lower() or "app"
        if metric_scope_n not in _METRIC_SCOPES:
            return _err(f"metric_scope must be one of {_METRIC_SCOPES}, got {metric_scope!r}")
        if metric_scope_n == "system" and (
            not app_metric.strip() or app_before is None or app_after is None
        ):
            return _err(
                "metric_scope=\"system\" requires the paired app_metric/app_before/"
                "app_after — a system-level optimization is only recordable together "
                "with proof it did not regress the app (non-degradation guard)"
            )

        delta = _pct(before, after, lower_is_better)
        if delta is None:
            verdict = "unknown"
        elif delta >= 5:
            verdict = "win"
        elif delta <= -5:
            verdict = "regression"
        else:
            verdict = "no_change"

        app_delta = None
        app_verdict = None
        guard_triggered = False
        if metric_scope_n == "system":
            app_delta = _pct(app_before, app_after, app_lower_is_better)
            if app_delta is None:
                app_verdict = "unknown"
            elif app_delta <= -_APP_REGRESSION_TOLERANCE_PCT:
                app_verdict = "regression"
            elif app_delta >= 5:
                app_verdict = "win"
            else:
                app_verdict = "no_change"
            # The non-degradation guard: a system-side win recorded alongside an
            # app-side regression is not a win — force the overall verdict to
            # reflect the app cost, and say so in notes so it's never missed.
            if app_verdict == "regression":
                guard_triggered = True
                verdict = "regression"
                guard_note = (
                    f"NON-DEGRADATION GUARD: system metric moved {delta}% but paired "
                    f"app metric ({app_metric}) regressed {app_delta}% "
                    f"(tolerance {_APP_REGRESSION_TOLERANCE_PCT}%) — verdict forced to "
                    f"regression."
                )
                notes = f"{guard_note} {notes}".strip()

        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "scope": scope_n, "level": _SCOPE_TO_LEVEL[scope_n],
            "system": system, "workload": workload, "software": software,
            "bottleneck": bottleneck, "change": change,
            "metric": metric, "before": before, "after": after,
            "lower_is_better": lower_is_better,
            "delta_pct": delta, "verdict": verdict,
            "citation": citation,
            "citation_type": (citation_type.strip().lower()
                              if citation_type.strip().lower() in _CITATION_RANK
                              else _classify_citation(citation)),
            "run_id": run_id, "notes": notes,
            "metric_scope": metric_scope_n,
            "app_metric": app_metric, "app_before": app_before, "app_after": app_after,
            "app_delta_pct": app_delta, "app_verdict": app_verdict,
        }
        with _kb_path().open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
        msg = f"recorded {scope_n}/{metric_scope_n} result: {verdict} ({delta}%)"
        if guard_triggered:
            msg += " — NON-DEGRADATION GUARD reverted this to a regression"
        return _ok(msg, entry=entry, guard_triggered=guard_triggered)

    @mcp.tool()
    def opt_kb_render() -> str:
        """Render the KB into the ``dftracer-optimization-kb`` skill (one file per scope).

        Structured JSONL is what the tools query; the generated markdown is what a
        future agent reads when it loads the skill. Run after recording results so
        the knowledge is available to sessions that never call the tools.

        Returns:
            JSON with per-scope entry ``counts`` and the ``skill_dir`` written.
        """
        counts = _render_to_skill()
        return _ok(f"rendered KB: {counts}", counts=counts, skill_dir=str(_skill_dir()))

    @mcp.tool()
    def opt_proposal_table(
        proposals_json: str,
        system: str = "",
        workload: str = "",
        software: str = "",
    ) -> str:
        """Render optimization proposals as a citation-backed markdown table.

        Every row MUST carry a citation; uncited proposals are rejected rather than
        silently rendered, because an uncited proposal is a guess. Every row MUST also
        carry ``app_impact_pct`` and ``system_impact_pct`` — the POTENTIAL % of current
        application wall time the strategy could plausibly move (a bounded estimate
        capped by the relevant measured bottleneck bucket from the diagnoser) and the
        POTENTIAL effect on system-level throughput/bandwidth/utilization (network
        bytes moved, storage bandwidth, CPU-core utilization). Use ``0`` (not a missing
        field) for "not applicable" so every row sorts deterministically — never omit
        these two fields. Once a row is actually applied and measured, fill in
        ``actual_app_impact_pct`` / ``actual_system_impact_pct`` (real before/after
        delta) so the table reports **potential vs. actual side by side** — never
        overwrite the potential estimate with the actual result, both stay visible.

        Rows are sorted by a 50/50 weighted score of the POTENTIAL
        ``app_impact_pct``/``system_impact_pct`` (``0.5*app + 0.5*system``,
        descending) — this determines apply order: run the highest-potential row
        first, then re-rank remaining rows against the post-apply diagnosis before
        picking the next one, since one applied change can shift which bottleneck is
        now largest. Citation quality (paper > docs > web > session) is the tiebreaker
        for equal scores.

        Each row is cross-referenced against the KB, so the table shows whether this
        exact lever has already been tried on this system/workload — and what it did.

        Args:
            proposals_json: JSON list; each item needs ``level`` (L1/L2/L3),
                ``bottleneck``, ``change``, ``expected_delta``, ``citation``,
                ``app_impact_pct`` (potential, number, 0 if N/A),
                ``system_impact_pct`` (potential, number, 0 if N/A),
                ``actual_app_impact_pct`` (optional, filled in after measuring),
                ``actual_system_impact_pct`` (optional, filled in after measuring).
            system / workload / software: context for the prior-result lookup.

        Returns:
            JSON with ``markdown`` (the table), ``accepted``, and ``rejected``
            (uncited proposals, with the reason).
        """
        try:
            props = json.loads(proposals_json)
        except json.JSONDecodeError as exc:
            return _err(f"proposals_json is not valid JSON: {exc}")
        if not isinstance(props, list):
            return _err("proposals_json must be a JSON list")

        def _pct(v) -> float:
            return float(v) if isinstance(v, (int, float)) else 0.0

        def _pct_or_none(v):
            return float(v) if isinstance(v, (int, float)) else None

        accepted, rejected = [], []
        for p in props:
            cit = (p.get("citation") or "").strip()
            if not cit:
                rejected.append({**p, "reason": "no citation (paper > docs > web)"})
                continue
            p = dict(p)
            p["citation_type"] = _classify_citation(cit)
            p["app_impact_pct"] = _pct(p.get("app_impact_pct"))
            p["system_impact_pct"] = _pct(p.get("system_impact_pct"))
            p["actual_app_impact_pct"] = _pct_or_none(p.get("actual_app_impact_pct"))
            p["actual_system_impact_pct"] = _pct_or_none(p.get("actual_system_impact_pct"))
            p["weighted_score"] = 0.5 * p["app_impact_pct"] + 0.5 * p["system_impact_pct"]
            pr = _find_prior(p.get("change", ""), p.get("level", ""),
                             system, workload, software)
            if pr:
                g = pr.get("delta_pct")
                gain = f" ({g:+.1f}%)" if isinstance(g, (int, float)) else ""
                p["prior"] = f"{pr['verdict']}{gain}"
                p["prior_notes"] = pr.get("notes", "")
            else:
                p["prior"] = "untried here"
            accepted.append(p)

        accepted.sort(key=lambda p: (-p["weighted_score"],
                                     _CITATION_RANK.index(p["citation_type"])
                                     if p["citation_type"] in _CITATION_RANK else 9,
                                     str(p.get("level", ""))))

        md = ["| # | Strategy | Description of Optimization | Potential App Impact | Potential System Impact | Actual App Impact | Actual System Impact | Weighted Score | Citation | Type | Prior result here | Status | Why (if not applied) |\n",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"]
        for i, p in enumerate(accepted, 1):
            strategy = f"{p.get('level','')}: {p.get('bottleneck','')}".strip(": ")
            actual_app = f"{p['actual_app_impact_pct']:.0f}%" if p["actual_app_impact_pct"] is not None else "—"
            actual_sys = f"{p['actual_system_impact_pct']:.0f}%" if p["actual_system_impact_pct"] is not None else "—"
            md.append(
                f"| {i} | {strategy} "
                f"| {p.get('change','')} | {p['app_impact_pct']:.0f}% "
                f"| {p['system_impact_pct']:.0f}% | {actual_app} | {actual_sys} "
                f"| {p['weighted_score']:.1f} "
                f"| {p.get('citation','')} | {p['citation_type']} | {p['prior']} "
                f"| {p.get('status','proposed')} | {p.get('skip_reason','')} |\n"
            )
        note = ("\nApply **one row at a time, in table order (highest weighted score "
                "first)**, measure, then `opt_kb_record` the result before moving to "
                "the next — otherwise the attribution is worthless. Potential/System "
                "Impact are bounded estimates capped by the relevant measured "
                "bottleneck bucket; Actual columns stay '—' until a real before/after "
                "measurement exists, then both potential and actual stay visible side "
                "by side (never overwrite one with the other).\n"
                "Every considered candidate belongs in this table, applied or not: set "
                "`status` to `applied`/`not-applied`/`deferred` and fill `skip_reason` for "
                "anything not applied (e.g. doesn't match the diagnosed bottleneck, no time/"
                "budget this pass, superseded by a stronger same-layer candidate, KB already "
                "shows it's a no-op here). Never omit a candidate from the table just because "
                "you decided not to run it.\n"
                "After every row in this table has been applied (or explicitly skipped), "
                "re-run the diagnoser against a fresh trace and compare the bottleneck list "
                "to the one this table was built from: report whether the ranking shifted "
                "(a bottleneck that was #2 may now be #1 once #1 is fixed), estimate the "
                "next round's potential impact ceiling, and ask the user whether to keep "
                "iterating before starting another pass — do not loop automatically.\n")
        return _ok(f"{len(accepted)} accepted, {len(rejected)} rejected (uncited)",
                   markdown="".join(md) + note,
                   accepted=accepted, rejected=rejected)

    @mcp.tool()
    def session_memory_retrieve(
        metric: str,
        sys_context: str = "",
        category: str = "",
        k: int = 3,
    ) -> str:
        """Read Tier-2 project memory for citations/strategies already
        validated for this (or a similar) bottleneck, before issuing a live
        arXiv/Semantic Scholar search.

        This is the "retrieve before you search" half of the agentic loop:
        call this first for every bottleneck in
        ``session_optimization_iteration``'s literature step; only fall back
        to a live query if nothing scores above the relevance threshold.

        Args:
            metric:      Bottleneck metric name (e.g. "small_io", "comm_wait").
            sys_context: Hardware/filesystem context string (from
                         ``_build_sys_context`` / the iteration tool's
                         ``system_context`` field) to prefer citations proven
                         on similar systems.
            category:    Optional explicit category override (io |
                         communication | memory | compute); inferred from
                         *metric* if omitted.
            k:           Max results to return (default 3).

        Returns:
            JSON with ``status``, ``matches`` (records with
            ``retrieval_score``, ``uses``, ``successes``, ``failures``,
            ``citation``), and ``count``.
        """
        matches = _memory_retrieve(metric, sys_context, category, k=k)
        return _ok(
            f"{len(matches)} memory match(es) for '{metric}'.",
            matches=matches,
            count=len(matches),
        )

    @mcp.tool()
    def session_memory_write(
        metric: str,
        sys_context: str,
        strategy_title: str,
        citation_json: str,
        source: str = "searched",
        outcome: Optional[str] = None,
    ) -> str:
        """Write path: record that *strategy_title* (backed by *citation_json*)
        was tried for *metric* on *sys_context*, optionally with an observed
        *outcome*.

        Args:
            metric:         Bottleneck metric name.
            sys_context:    Hardware/filesystem context string.
            strategy_title: The proposal/paper title used.
            citation_json:  JSON object with authors/title/venue/year/url.
            source:         "searched" (arXiv) or "builtin" (WisIO/Drishti/etc).
            outcome:        "improved" | "resolved" | "regressed" | omit if
                            not yet known.

        Returns:
            JSON with the upserted record.
        """
        try:
            citation = json.loads(citation_json) if citation_json else {}
        except Exception as exc:
            return _err(f"citation_json is not valid JSON: {exc}")
        record = _memory_write(metric, sys_context, strategy_title, citation, source, outcome)
        return _ok(f"Recorded memory entry for '{metric}'.", record=record)

    @mcp.tool()
    def session_memory_reflect(run_id: str, iteration: int = -1) -> str:
        """Agentic reflection: convert one iteration's observed bottleneck
        delta (improved/regressed/resolved) into persisted Tier-2 verdicts
        for the citations used in that iteration.

        Call this right after ``session_optimization_iteration`` so the next
        call to ``session_memory_retrieve`` — in this run or a future one —
        already knows whether the cited fix actually worked here.

        Args:
            run_id:    Session identifier.
            iteration: Which iteration's delta to reflect on (-1 = latest).

        Returns:
            JSON with ``written`` (count) and ``detail`` (per-metric outcome
            written).
        """
        result = _memory_reflect(run_id, iteration)
        return _ok(
            f"Reflected on iteration {iteration}: {result['written']} verdict(s) written.",
            **result,
        )

    @mcp.tool()
    def session_memory_stats() -> str:
        """Summarize the Tier-2 project memory store — counts, top strategies
        by confidence, and per-category breakdown. Useful for inspecting what
        the optimization loop has learned across all sessions."""
        records = _load_memory()
        if not records:
            return _ok("Memory store is empty.", count=0, by_category={}, top=[])

        by_category: Dict[str, int] = {}
        for r in records:
            by_category[r.get("category", "io")] = by_category.get(r.get("category", "io"), 0) + 1

        def _confidence(r: Dict[str, Any]) -> float:
            uses = max(1, r.get("uses", 0))
            return (r.get("successes", 0) - r.get("failures", 0)) / uses

        top = sorted(records, key=_confidence, reverse=True)[:10]
        top_summary = [
            {
                "metric": r.get("metric"),
                "strategy_title": r.get("strategy_title"),
                "uses": r.get("uses"),
                "successes": r.get("successes"),
                "failures": r.get("failures"),
                "confidence": round(_confidence(r), 3),
            }
            for r in top
        ]
        return _ok(
            f"{len(records)} record(s) in Tier-2 memory.",
            count=len(records),
            by_category=by_category,
            top=top_summary,
        )


    @mcp.tool()
    def dftracer_opt_kb_check(entry_json: str = "", kb_file: str = "") -> str:
        """Deterministically validate optimization knowledge-base entries before (or after) opt_kb_record.

        Use before recording a result, or to audit the KB file. entry_json is one entry or a list (the same fields
        opt_kb_record takes or stores); kb_file is the optimization_kb.jsonl to audit line by line.
        Checks: scope is workload/software/system (L1/L2/L3); a usable citation; system scope names the system and
        workload scope the workload; change and metric given; before/after are measured numbers; metric_scope=system
        carries the paired app metric (non-degradation guard); a stored verdict agrees with the numbers; a session:
        citation names the same run as run_id.

        Returns JSON with keys: status ("pass" | "fail" | "inconclusive" when nothing was given), checks (check, ok,
        detail, fix), failed, entries.
        """
        return _opt_kb_check(entry_json, kb_file)
