"""Cross-session optimization knowledge base, partitioned by level.

An optimization study is only worth its cost if the next study starts from it.
Today each session re-derives the same conclusions: that Cray MPICH ignores
``cb_nodes``, that a serial HDF5 IO unit makes ``useCollectiveHDF5`` inert, that
Lustre striping must be set before the first file is created. Those findings
belong to three different scopes, and conflating them is what makes them
unreusable:

* **system**   (L3) — a property of the machine/filesystem. Transfers to any
  workload on that system, and to no workload off it.
* **software** (L2) — a property of a library/runtime (HDF5, MPI-IO, ROMIO).
  Transfers to any workload linking that software, on any system.
* **workload** (L1) — a property of the application itself. Transfers to that
  application anywhere.

This module stores every *measured* optimization attempt with its scope, its
citation, and its actual before/after numbers, then lets the next session ask
"what has already been tried here?" before proposing anything.

Design rules, learned the hard way:

1. **Only measured results are recorded.** A proposal is a hypothesis; an entry
   requires ``metric``, ``before``, ``after``. Failures are recorded too — knowing
   ``cb_nodes=8`` did nothing is as valuable as knowing what did.
2. **Every entry carries a citation.** Paper (preferred) > official docs > web.
   ``session:<run_id>`` is allowed ONLY for a result measured in this pipeline,
   and is marked as such so it is never mistaken for external evidence.
3. **Recall precedes proposal.** ``opt_kb_lookup`` is step 1 of the loop.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from ..session.workspace import _workspaces_root, _ok, _err


#: The three optimization scopes. Level tags (L1/L2/L3) are kept as an alias
#: because the existing proposal generator speaks in levels.
_SCOPES = ("workload", "software", "system")
_LEVEL_TO_SCOPE = {"L1": "workload", "L2": "software", "L3": "system"}
_SCOPE_TO_LEVEL = {v: k for k, v in _LEVEL_TO_SCOPE.items()}

#: A second, orthogonal axis to `scope` (layer: workload/software/system —
#: "who inherits this finding"). `metric_scope` answers a different question:
#: "which metric moved" — the app's own trace (epoch/I-O time, app-observed
#: bandwidth) vs a system-level outcome (aggregate achieved filesystem
#: bandwidth, reduced filesystem load — currently computed as a trace-derived
#: proxy; see dftracer-io-optimization SKILL.md for the caveat). A `system`
#: metric_scope entry is only worth keeping if it did not come at the app's
#: expense — enforced by the paired app_metric requirement on opt_kb_record.
_METRIC_SCOPES = ("app", "system")

#: A system metric is allowed to improve at the app's expense only within this
#: tolerance (percent). Anything worse forces verdict="regression" regardless
#: of how good the system-side number looks — the non-degradation guard.
_APP_REGRESSION_TOLERANCE_PCT = 2.0

#: Citation quality, best first. Proposals and entries are ranked by this.
_CITATION_RANK = ("paper", "docs", "web", "session")


def _kb_path() -> Path:
    """Append-only JSONL store, a sibling of every per-run workspace."""
    p = _workspaces_root() / "_memory"
    p.mkdir(parents=True, exist_ok=True)
    return p / "optimization_kb.jsonl"


def _load_kb() -> List[Dict[str, Any]]:
    path = _kb_path()
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # a corrupt line must not sink the whole KB
    return out


def _classify_citation(citation: str) -> str:
    """Classify a citation string into paper / docs / web / session."""
    c = (citation or "").strip().lower()
    if not c:
        return ""
    if c.startswith("session:"):
        return "session"
    if any(k in c for k in ("arxiv", "doi.org", "10.", "acm.org", "ieee",
                            "usenix", "sc.supercomputing", "dl.acm")):
        return "paper"
    if any(k in c for k in ("docs.", "readthedocs", "/doc/", "documentation",
                            "manual", "hdfgroup.org", "mpich.org", "lustre.org",
                            "wiki.")):
        return "docs"
    if c.startswith("http"):
        return "web"
    return "web"


def _pct(before: float, after: float, lower_is_better: bool) -> Optional[float]:
    """Percent improvement. Positive always means 'better'."""
    try:
        before = float(before)
        after = float(after)
    except (TypeError, ValueError):
        return None
    if before == 0:
        return None
    delta = (before - after) / abs(before) * 100.0
    return round(delta if lower_is_better else -delta, 1)


def _software_set(software: str) -> set:
    """Software context may name several libraries (``"hdf5,mpi-io,lustre"``).

    A run links more than one library, and a finding about MPI-IO is relevant to a
    session that also uses HDF5 on top of it. Matching a single string would hide
    exactly the cross-layer knowledge the KB exists to carry.
    """
    return {s.strip().lower() for s in software.split(",") if s.strip()}


def _relevance(entry: Dict[str, Any], system: str, workload: str,
               software: str) -> int:
    """Score how much a past entry applies here.

    A system-scoped finding is worthless off that system; a workload-scoped one
    is worthless for a different app. Software findings travel furthest. The
    score encodes that so ``opt_kb_lookup`` surfaces transferable knowledge first
    rather than whatever happened to be recorded last.
    """
    scope = entry.get("scope", "")
    score = 0
    if scope == "system":
        if entry.get("system") and entry["system"] == system:
            score += 100
        else:
            return 0                       # never transfers off its system
    elif scope == "workload":
        if entry.get("workload") and entry["workload"] == workload:
            score += 100
        else:
            return 0                       # never transfers to another app
    elif scope == "software":
        want = _software_set(software)
        have = (entry.get("software") or "").lower()
        if want and have in want:
            score += 80
        elif not want:
            score += 40                    # no context given — still worth showing
        else:
            return 0
    # Bonuses for matching secondary context.
    if entry.get("system") == system:
        score += 10
    if entry.get("workload") == workload:
        score += 10
    if entry.get("verdict") == "win":
        score += 5
    return score


def _lookup(system: str, workload: str, software: str, scope: str,
            bottleneck: str, limit: int, metric_scope: str = "") -> List[Dict[str, Any]]:
    rows = _load_kb()
    if scope:
        scope = _LEVEL_TO_SCOPE.get(scope.upper(), scope.lower())
        rows = [r for r in rows if r.get("scope") == scope]
    if metric_scope:
        rows = [r for r in rows if r.get("metric_scope", "app") == metric_scope]
    if bottleneck:
        pat = re.compile(re.escape(bottleneck), re.I)
        rows = [r for r in rows
                if pat.search(r.get("bottleneck", "") + " " + r.get("change", ""))]
    scored = [(_relevance(r, system, workload, software), r) for r in rows]
    scored = [(s, r) for s, r in scored if s > 0]
    scored.sort(key=lambda x: (-x[0], -(x[1].get("delta_pct") or 0)))
    return [dict(r, _relevance=s) for s, r in scored[:limit]]


_TOKEN_RE = re.compile(r"[a-z0-9_+]+")


def _tokens(s: str) -> set:
    return {w for w in _TOKEN_RE.findall((s or "").lower()) if len(w) > 2}


def _find_prior(change: str, level: str, system: str, workload: str,
                software: str) -> Optional[Dict[str, Any]]:
    """Find the recorded entry that describes the SAME lever as *change*.

    Exact substring matching never fires: a proposal says "rebuild with
    +parallelIO" while the recorded entry says "rebuild with `+parallelIO`
    (parallel HDF5 IO unit; -auto selects serial/PM)". Compare distinctive tokens
    instead, so a proposal is correctly reported as already-tried.
    """
    scope = _LEVEL_TO_SCOPE.get(str(level).upper(), "")
    cand = _lookup(system, workload, software, scope, "", 200)
    want = _tokens(change)
    if not want:
        return None
    best, best_score = None, 0.0
    for r in cand:
        have = _tokens(r.get("change", ""))
        if not have:
            continue
        jac = len(want & have) / len(want | have)
        if jac > best_score:
            best, best_score = r, jac
    return best if best_score >= 0.25 else None


def _render_rows(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "_no prior experiments recorded for this scope_\n"
    head = ("| Scope | Metric Scope | System | Workload | Software | Change | Metric | Before | After "
            "| Gain | Verdict | App Metric | App Gain | Citation |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
    out = [head]
    for r in rows:
        gain = r.get("delta_pct")
        gain = f"{gain:+.1f}%" if isinstance(gain, (int, float)) else "n/a"
        app_gain = r.get("app_delta_pct")
        app_gain = f"{app_gain:+.1f}%" if isinstance(app_gain, (int, float)) else "-"
        out.append(
            f"| {r.get('scope','')} | {r.get('metric_scope','app')} | {r.get('system','') or '-'} "
            f"| {r.get('workload','') or '-'} | {r.get('software','') or '-'} "
            f"| {r.get('change','')} | {r.get('metric','')} "
            f"| {r.get('before','')} | {r.get('after','')} | {gain} "
            f"| {r.get('verdict','')} | {r.get('app_metric','') or '-'} | {app_gain} "
            f"| {r.get('citation','')} |\n"
        )
    return "".join(out)


def _skill_dir() -> Path:
    """Locate the packaged skills directory for the KB skill."""
    here = Path(__file__).resolve()
    # .../src/dftracer_agents/mcp_tools/tools/optimizations/knowledge_base.py
    pkg = here.parents[3]                      # .../src/dftracer_agents
    d = pkg / ".agents" / "skills" / "dftracer-optimization-kb"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _render_to_skill() -> Dict[str, int]:
    rows = _load_kb()
    counts: Dict[str, int] = {}
    d = _skill_dir()
    for scope in _SCOPES:
        sub = [r for r in rows if r.get("scope") == scope]
        sub.sort(key=lambda r: -(r.get("delta_pct") or 0))
        counts[scope] = len(sub)
        level = _SCOPE_TO_LEVEL[scope]
        body = (
            f"# {scope.capitalize()}-centric optimizations ({level})\n\n"
            f"Measured results carried across sessions. "
            f"{'Transfers to any workload on this system.' if scope=='system' else ''}"
            f"{'Transfers to any workload linking this software, on any system.' if scope=='software' else ''}"
            f"{'Transfers to this application on any system.' if scope=='workload' else ''}\n\n"
            f"Auto-generated by `opt_kb_render` from `_memory/optimization_kb.jsonl`. "
            f"Do not hand-edit; record with `opt_kb_record`.\n\n"
            + _render_rows(sub)
        )
        (d / f"{scope}.md").write_text(body)

    (d / "SKILL.md").write_text(
        "---\n"
        "name: dftracer-optimization-kb\n"
        "description: >\n"
        "  Cross-session, citation-backed knowledge base of every MEASURED dftracer\n"
        "  optimization, partitioned into system-centric (L3), software-centric (L2),\n"
        "  and workload-centric (L1) findings. Load this FIRST in any optimization\n"
        "  loop to avoid re-deriving what is already known.\n"
        "---\n\n"
        "# dftracer optimization knowledge base\n\n"
        "**Step 1 of every optimization loop is recall, not proposal.** Call\n"
        "`opt_kb_lookup(system=..., workload=..., software=..., scope=...)` before\n"
        "generating any proposal, and cite prior results in the proposal table.\n\n"
        "Scopes and what they transfer to:\n\n"
        "| Scope | Level | Transfers to | File |\n"
        "| --- | --- | --- | --- |\n"
        "| system | L3 | any workload **on that system** | [system.md](system.md) |\n"
        "| software | L2 | any workload **linking that software**, any system | [software.md](software.md) |\n"
        "| workload | L1 | that application, **any system** | [workload.md](workload.md) |\n\n"
        f"Recorded: {counts.get('system',0)} system, {counts.get('software',0)} software, "
        f"{counts.get('workload',0)} workload entries.\n\n"
        "## A second, orthogonal axis: metric_scope\n\n"
        "`scope` (above) answers *who inherits* a finding. `metric_scope` answers a\n"
        "different question — *which metric moved*:\n\n"
        "| metric_scope | What it measures |\n"
        "| --- | --- |\n"
        "| `app` (default) | The app's own trace: epoch/I-O time, app-observed bandwidth. |\n"
        "| `system` | A filesystem/system-level outcome: aggregate achieved bandwidth,\n"
        "  reduced filesystem load — currently a trace-derived proxy, not real\n"
        "  OST/MDT-side telemetry (this pipeline has no Lustre-admin monitoring access). |\n\n"
        "**Non-degradation guard (MANDATORY):** a `metric_scope=\"system\"` entry MUST\n"
        "carry a paired `app_metric`/`app_before`/`app_after` — `opt_kb_record` rejects\n"
        f"one without it. If the paired app metric regressed more than "
        f"{_APP_REGRESSION_TOLERANCE_PCT}%, the verdict is force-set to `regression`\n"
        "regardless of how good the system-side number looks. A system optimization\n"
        "that costs the app is not a win — never apply/keep one where this guard fired.\n\n"
        "## Rules\n\n"
        "1. Record only **measured** results — `metric`, `before`, `after` are required.\n"
        "   Record failures too: knowing a lever did nothing is a result.\n"
        "2. Every entry carries a **citation**: paper (preferred) > official docs > web.\n"
        "   `session:<run_id>` marks a result measured in-house, never external evidence.\n"
        "3. Apply optimizations **one at a time** and measure each, or the attribution\n"
        "   is worthless.\n"
        "4. A `metric_scope=\"system\"` entry always carries its paired app-metric proof\n"
        "   — see the non-degradation guard above.\n"
    )
    return counts


def register_optimization_kb_tools(mcp: FastMCP) -> None:
    """Register ``opt_kb_lookup``, ``opt_kb_record``, ``opt_kb_render``, ``opt_proposal_table``."""

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
        silently rendered, because an uncited proposal is a guess. Rows are sorted
        by citation quality (paper > docs > web > session) and then by level, so the
        best-evidenced change is applied first.

        Each row is cross-referenced against the KB, so the table shows whether this
        exact lever has already been tried on this system/workload — and what it did.

        Args:
            proposals_json: JSON list; each item needs ``level`` (L1/L2/L3),
                ``bottleneck``, ``change``, ``expected_delta``, ``citation``.
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

        accepted, rejected = [], []
        for p in props:
            cit = (p.get("citation") or "").strip()
            if not cit:
                rejected.append({**p, "reason": "no citation (paper > docs > web)"})
                continue
            p = dict(p)
            p["citation_type"] = _classify_citation(cit)
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

        accepted.sort(key=lambda p: (_CITATION_RANK.index(p["citation_type"])
                                     if p["citation_type"] in _CITATION_RANK else 9,
                                     str(p.get("level", ""))))

        md = ["| # | Level | Bottleneck | Proposed change | Expected gain | Evidence | Type | Prior result here |\n",
              "| --- | --- | --- | --- | --- | --- | --- | --- |\n"]
        for i, p in enumerate(accepted, 1):
            md.append(
                f"| {i} | {p.get('level','')} | {p.get('bottleneck','')} "
                f"| {p.get('change','')} | {p.get('expected_delta','')} "
                f"| {p.get('citation','')} | {p['citation_type']} | {p['prior']} |\n"
            )
        note = ("\nApply **one row at a time**, measure, then `opt_kb_record` the result "
                "before moving to the next — otherwise the attribution is worthless.\n")
        return _ok(f"{len(accepted)} accepted, {len(rejected)} rejected (uncited)",
                   markdown="".join(md) + note,
                   accepted=accepted, rejected=rejected)
